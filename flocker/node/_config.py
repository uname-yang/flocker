# Copyright Hybrid Logic Ltd.  See LICENSE file for details.
# -*- test-case-name: flocker.node.test.test_config -*-

"""
APIs for parsing and validating configuration.
"""

from __future__ import unicode_literals, absolute_import

import os
import types
import yaml

from twisted.python.filepath import FilePath

from ._model import (
    Application, AttachedVolume, Deployment, Link,
    DockerImage, Node, Port
)


class ConfigurationError(Exception):
    """
    Some part of the supplied configuration was wrong.

    The exception message will include some details about what.
    """


def _check_type(value, types, description, application_name):
    """
    Checks ``value`` has type in ``types``.

    :param value: Value whose type is to be checked
    :param tuple types: Tuple of types value can be.
    :param str description: Description of expected type.
    :param application_name unicode: Name of application whose config
        contains ``value``.

    :raises ConfigurationError: If ``value`` is not of type in ``types``.
    """
    if not isinstance(value, types):
        raise ConfigurationError(
            "Application '{application_name}' has a config "
            "error. {description}; got type '{type}'.".format(
                application_name=application_name,
                description=description,
                type=type(value).__name__,
            ))


class FigConfiguration(object):
    """
    Validate and parse a fig-style application configuration.
    """
    def __init__(self, application_configuration):
        """
        Initializes ``FigConfiguration`` attributes and validates config.

        :param dict application_configuration: The intermediate
            configuration representation to load into ``Application``
            instances.  See :ref:`Configuration` for details.
        
        Attributes initialized in this method are:
        
        self._application_configuration: the application_configuration
            parameter
        
        self._application_names: A ``list`` of keys in
            application_configuration representing all application
            names.
        
        self._applications: The ``dict`` of ``Application`` objects
            after parsing.
        
        self._application_links: ``dict`` acting as an internal map
            of links to create between applications, this serves as an
            intermediary when parsing applications, since an application
            name specified in a link may not have been parsed at the point
            the link is encountered.
        
        self._validated: A ``bool`` indicating whether or not the supplied
            configuration has been validated as Fig-format. Note this does
            not indicate the actual configuration is valid, only that it
            meets the minimum requirements to be interpreted as a Fig config.
        
        self._possible_identifiers: A ``dict`` of keys that may identify a
            dictionary of parsed YAML as being Fig-format.
            
        self._unsupported_keys: A ``dict`` of keys representing Fig config
            directives that are not yet supported by Flocker.
            
        self._allowed_keys: A ``dict`` representing all the keys that are
            supported and therefore allowed to appear in a single Fig service
            definition.
        """
        if not isinstance(application_configuration, dict):
            raise ConfigurationError(
                "Application configuration must be a dictionary, got {type}.".
                format(type=type(application_configuration).__name__)
            )
        self._application_configuration = application_configuration
        self._application_names = self._application_configuration.keys()
        self._applications = {}
        self._application_links = {}
        self._validated = False
        self._possible_identifiers = {'image', 'build'}
        self._unsupported_keys = {
            "working_dir", "entrypoint", "user", "hostname",
            "domainname", "mem_limit", "privileged", "dns", "net",
            "volumes_from", "expose", "command"
        }
        self._allowed_keys = {
            "image", "environment", "ports",
            "links", "volumes"
        }

    def applications(self):
        """
        Returns the ``Application`` instances parsed from the supplied
        configuration.

        This method should only be called once, in that calling it
        multiple times will re-parse an already parsed config.

        :returns: A ``dict`` mapping application names to ``Application``
            instances.
        """
        self._parse()
        return self._applications

    def is_valid_format(self):
        """
        Detect if the supplied application configuration is in fig-compatible
        format.

        A fig-style configuration is defined as:
        Overall application configuration is of type dictionary, containing
        one or more keys which each contain a further dictionary, which
        contain exactly one "image" key or "build" key.
        http://www.fig.sh/yml.html

        :raises ConfigurationError: if the config is valid fig-format but
            not a valid config.

        :returns: A ``bool`` indicating ``True`` for a fig-style configuration
            or ``False`` if fig-style is not detected.
        """
        self._validated = False
        for application_name, config in (
                self._application_configuration.items()):
            if isinstance(config, dict):
                required_keys = self._count_identifier_keys(config)
                if required_keys == 1:
                    self._validated = True
                elif required_keys > 1:
                    raise ConfigurationError(
                        ("Application '{app_name}' has a config error. "
                         "Must specify either 'build' or 'image'; found both.")
                        .format(app_name=application_name)
                    )
        return self._validated

    def _count_identifier_keys(self, config):
        """
        Counts how many of the keys that identify a single application
        as having a fig-format are found in the supplied application
        definition.

        :param dict config: A single application definition from
            the application_configuration dictionary.

        :returns: ``int`` representing the number of identifying keys found.
        """
        config_keys = set(config)
        return len(self._possible_identifiers & config_keys)

    def _validate_application_keys(self, application, config):
        """
        Checks that a single application definition contains no invalid
        or unsupported keys.

        :param bytes application: The name of the application this config
            is mapped to.

        :param dict config: A single application definition from
            the application_configuration dictionary.

        :raises ValueError: if any invalid or unsupported keys found.

        :returns: ``None``
        """
        _check_type(config, dict,
                    "Application configuration must be dictionary",
                    application)
        if self._count_identifier_keys(config) == 0:
            raise ValueError(
                ("Application configuration must contain either an "
                 "'image' or 'build' key.")
            )
        if 'build' in config:
            raise ValueError(
                "'build' is not supported yet; please specify 'image'."
            )
        present_keys = set(config)
        invalid_keys = present_keys - self._allowed_keys
        present_unsupported_keys = self._unsupported_keys & present_keys
        if present_unsupported_keys:
            raise ValueError(
                "Unsupported fig keys found: {keys}".format(
                    keys=', '.join(sorted(present_unsupported_keys))
                )
            )
        if invalid_keys:
            raise ValueError(
                "Unrecognised keys: {keys}".format(
                    keys=', '.join(invalid_keys)
                )
            )

    def _parse_app_environment(self, application, environment):
        """
        Validate and parse the environment portion of an application
        configuration.

        :param bytes application: The name of the application this config
            is mapped to.

        :param dict environment: A dictionary of environment variable
            names and values.

        :raises ConfigurationError: if the environment config does
            not validate.

        :returns: A ``frozenset`` of environment variable name/value
            pairs.
        """
        _check_type(environment, dict,
                    "'environment' must be a dictionary",
                    application)
        for var, val in environment.items():
            _check_type(
                val, (str, unicode,),
                ("'environment' value for '{var}' must be a string"
                 .format(var=var)),
                application
            )
        return frozenset(environment.items())

    def _parse_app_volumes(self, application, volumes):
        """
        Validate and parse the volumes portion of an application
        configuration.

        :param bytes application: The name of the application this config
            is mapped to.

        :param list volumes: A list of ``str`` values giving absolute
            paths where a volume should be mounted inside the application.

        :raises ConfigurationError: if the volumes config does not validate.

        :returns: A ``AttachedVolume`` instance.
        """
        _check_type(volumes, list,
                    "'volumes' must be a list",
                    application)
        for volume in volumes:
            if not isinstance(volume, (str, unicode,)):
                raise ConfigurationError(
                    ("Application '{application}' has a config "
                     "error. 'volumes' values must be string; got "
                     "type '{type}'.").format(
                         application=application,
                         type=type(volume).__name__)
                )
        if len(volumes) > 1:
            raise ConfigurationError(
                ("Application '{application}' has a config "
                 "error. Only one volume per application is "
                 "supported at this time.").format(
                     application=application)
            )
        volume = AttachedVolume(
            name=application,
            mountpoint=FilePath(volumes.pop())
        )
        return volume

    def _parse_app_ports(self, application, ports):
        """
        Validate and parse the ports portion of an application
        configuration.

        :param bytes application: The name of the application this config
            is mapped to.

        :param list ports: A list of ``str`` values mapping ports that
            should be exposed by the application container to the host.

        :raises ConfigurationError: if the ports config does not validate.

        :returns: A ``list`` of ``Port`` instances.
        """
        return_ports = list()
        _check_type(ports, list,
                    "'ports' must be a list",
                    application)
        for port in ports:
            parsed_ports = port.split(':')
            if len(parsed_ports) != 2:
                raise ConfigurationError(
                    ("Application '{application}' has a config "
                     "error. 'ports' must be list of string "
                     "values in the form of "
                     "'host_port:container_port'.").format(
                         application=application)
                )
            try:
                parsed_ports = [int(p) for p in parsed_ports]
            except ValueError:
                raise ConfigurationError(
                    ("Application '{application}' has a config "
                     "error. 'ports' value '{ports}' could not "
                     "be parsed in to integer values.")
                    .format(
                        application=application,
                        ports=port)
                )
            return_ports.append(
                Port(
                    internal_port=parsed_ports[1],
                    external_port=parsed_ports[0]
                )
            )
        return return_ports

    def _parse_app_links(self, application, links):
        """
        Validate and parse the links portion of an application
        configuration and store the links in the internal links map.

        :param bytes application: The name of the application this config
            is mapped to.

        :param list links: A list of ``str`` values specifying the names
            of applications that this application should link to.

        :raises ConfigurationError: if the links config does not validate.

        :returns: ``None``
        """
        _check_type(links, list,
                    "'links' must be a list",
                    application)
        for link in links:
            if not isinstance(link, (str, unicode,)):
                raise ConfigurationError(
                    ("Application '{application}' has a config "
                     "error. 'links' must be a list of "
                     "application names with optional :alias.")
                    .format(application=application)
                )
            parsed_link = link.split(':')
            local_link = parsed_link[0]
            aliased_link = local_link
            if len(parsed_link) == 2:
                aliased_link = parsed_link[1]
            if local_link not in self._application_names:
                raise ConfigurationError(
                    ("Application '{application}' has a config "
                     "error. 'links' value '{link}' could not be "
                     "mapped to any application; application "
                     "'{link}' does not exist.").format(
                         application=application,
                         link=link)
                )
            self._application_links[application].append({
                'target_application': local_link,
                'alias': aliased_link,
            })

    def _link_applications(self):
        """
        Iterate through the internal links map and create a
        frozenset of ``Link`` instances in each application, mapping
        the link name and alias to the ports of the target linked
        application.

        :returns: ``None``
        """
        for application_name, link in self._application_links.items():
            self._applications[application_name].links = []
            for link_definition in link:
                target_application_ports = self._applications[
                    link_definition['target_application']].ports
                target_ports_objects = iter(target_application_ports)
                for target_ports_object in target_ports_objects:
                    local_port = target_ports_object.internal_port
                    remote_port = target_ports_object.external_port
                    self._applications[application_name].links.append(
                        Link(local_port=local_port,
                             remote_port=remote_port,
                             alias=link_definition['alias'])
                    )
            self._applications[application_name].links = frozenset(
                self._applications[application_name].links)

    def _parse(self):
        """
        Validate and parse a given application configuration from fig's
        configuration format.

        :raises ConfigurationError: if there are validation errors.

        :returns: A ``dict`` mapping application names to ``Application``
            instances.

        """
        if not self._validated:
            if not self.is_valid_format():
                raise ConfigurationError(
                    "Supplied configuration does not appear to be Fig format."
                )
        for application_name, config in (
            self._application_configuration.items()
        ):
            try:
                self._validate_application_keys(application_name, config)
                _check_type(config['image'], (str, unicode,),
                            "'image' must be a string",
                            application_name)
                image_name = config['image']
                image = DockerImage.from_string(image_name)
                environment = None
                ports = []
                volume = None
                self._application_links[application_name] = []
                if 'environment' in config:
                    environment = self._parse_app_environment(
                        application_name,
                        config['environment']
                    )
                if 'volumes' in config:
                    volume = self._parse_app_volumes(
                        application_name,
                        config['volumes']
                    )
                if 'ports' in config:
                    ports = self._parse_app_ports(
                        application_name,
                        config['ports']
                    )
                if 'links' in config:
                    self._parse_app_links(
                        application_name,
                        config['links']
                    )
                self._applications[application_name] = Application(
                    name=application_name,
                    image=image,
                    volume=volume,
                    ports=frozenset(ports),
                    links=frozenset(),
                    environment=environment
                )
            except ValueError as e:
                raise ConfigurationError(
                    ("Application '{application_name}' has a config error. "
                     "{message}".format(application_name=application_name,
                                        message=e.message))
                )
        self._link_applications()


class Configuration(object):
    """
    Validate and parse configurations.
    """
    def __init__(self, lenient=False):
        """
        :param bool lenient: If ``True`` don't complain about certain
            deficiencies in the output of ``flocker-reportstate``, In
            particular https://github.com/ClusterHQ/flocker/issues/289 means
            the mountpoint is unknown.
        """
        self._lenient = lenient

    def _parse_environment_config(self, application_name, config):
        """
        Validate and return an application config's environment variables.

        :param unicode application_name: The name of the application.

        :param dict config: The config of a single ``Application`` instance,
            as extracted from the ``applications`` ``dict`` in
            ``_applications_from_configuration``.

        :raises ConfigurationError: if the ``environment`` element of
            ``config`` is not a ``dict`` or ``dict``-like value.

        :returns: ``None`` if there is no ``environment`` element in the
            config, or the ``frozenset`` of environment variables if there is,
            in the form of a ``frozenset`` of ``tuple`` \s mapped to
            (key, value)

        """
        environment = config.pop('environment', None)
        if environment:
            _check_type(value=environment, types=(dict,),
                        description="'environment' must be a dictionary of "
                                    "key/value pairs",
                        application_name=application_name)
            for key, value in environment.iteritems():
                # We should normailzie strings to either bytes or unicode here
                # https://github.com/ClusterHQ/flocker/issues/636
                _check_type(value=key, types=types.StringTypes,
                            description="Environment variable name "
                                        "must be a string",
                            application_name=application_name)
                _check_type(value=value, types=types.StringTypes,
                            description="Environment variable '{key}' "
                                        "must be a string".format(key=key),
                            application_name=application_name)
            environment = frozenset(environment.items())
        return environment

    def _parse_link_configuration(self, application_name, config):
        """
        Validate and retrun an application config's links.

        :param unicode application_name: The name of the application

        :param dict config: The ``links`` configuration stanza of this
            application.

        :returns: A ``frozenset`` of ``Link``s specfied for this application.
        """
        links = []
        _check_type(value=config, types=(list,),
                    description="'links' must be a list of dictionaries",
                    application_name=application_name)
        try:
            for link in config:
                _check_type(value=link, types=(dict,),
                            description="Link must be a dictionary",
                            application_name=application_name)

                try:
                    local_port = link.pop('local_port')
                    _check_type(value=local_port, types=(int,),
                                description="Link's local port must be an int",
                                application_name=application_name)
                except KeyError:
                    raise ValueError("Missing local port.")

                try:
                    remote_port = link.pop('remote_port')
                    _check_type(value=remote_port, types=(int,),
                                description="Link's remote port "
                                            "must be an int",
                                application_name=application_name)
                except KeyError:
                    raise ValueError("Missing remote port.")

                try:
                    # We should normailzie strings to either bytes or unicode
                    # here. https://github.com/ClusterHQ/flocker/issues/636
                    alias = link.pop('alias')
                    _check_type(value=alias, types=types.StringTypes,
                                description="Link alias must be a string",
                                application_name=application_name)
                except KeyError:
                    raise ValueError("Missing alias.")

                if link:
                    raise ValueError(
                        "Unrecognised keys: {keys}.".format(
                            keys=', '.join(sorted(link))))
                links.append(Link(local_port=local_port,
                                  remote_port=remote_port,
                                  alias=alias))
        except ValueError as e:
            raise ConfigurationError(
                ("Application '{application_name}' has a config error. "
                 "Invalid links specification. {message}").format(
                     application_name=application_name, message=e.message))

        return frozenset(links)

    def _applications_from_flocker_configuration(
            self, application_configuration):
        """
        Validate and parse a given application configuration from flocker's
        configuration format.

        :param dict application_configuration: The intermediate configuration
            representation to load into ``Application`` instances.  See
            :ref:`Configuration` for details.

        :raises ConfigurationError: if there are validation errors.

        :returns: A ``dict`` mapping application names to ``Application``
            instances.
        """
        if u'applications' not in application_configuration:
            raise ConfigurationError("Application configuration has an error. "
                                     "Missing 'applications' key.")

        if u'version' not in application_configuration:
            raise ConfigurationError("Application configuration has an error. "
                                     "Missing 'version' key.")

        if application_configuration[u'version'] != 1:
            raise ConfigurationError("Application configuration has an error. "
                                     "Incorrect version specified.")

        applications = {}
        for application_name, config in (
                application_configuration['applications'].items()):
            try:
                image_name = config.pop('image')
            except KeyError as e:
                raise ConfigurationError(
                    ("Application '{application_name}' has a config error. "
                     "Missing value for '{message}'.").format(
                        application_name=application_name, message=e.message)
                )

            try:
                image = DockerImage.from_string(image_name)
            except ValueError as e:
                raise ConfigurationError(
                    ("Application '{application_name}' has a config error. "
                     "Invalid Docker image name. {message}").format(
                        application_name=application_name, message=e.message)
                )

            ports = []
            try:
                for port in config.pop('ports', []):
                    try:
                        internal_port = port.pop('internal')
                    except KeyError:
                        raise ValueError("Missing internal port.")
                    try:
                        external_port = port.pop('external')
                    except KeyError:
                        raise ValueError("Missing external port.")

                    if port:
                        raise ValueError(
                            "Unrecognised keys: {keys}.".format(
                                keys=', '.join(sorted(port.keys()))))
                    ports.append(Port(internal_port=internal_port,
                                      external_port=external_port))
            except ValueError as e:
                raise ConfigurationError(
                    ("Application '{application_name}' has a config error. "
                     "Invalid ports specification. {message}").format(
                        application_name=application_name, message=e.message)
                )

            links = self._parse_link_configuration(
                application_name, config.pop('links', []))

            volume = None
            if "volume" in config:
                try:
                    configured_volume = config.pop('volume')
                    try:
                        mountpoint = configured_volume['mountpoint']
                    except TypeError:
                        raise ValueError(
                            "Unexpected value: " + str(configured_volume)
                        )
                    except KeyError:
                        raise ValueError("Missing mountpoint.")

                    if not (self._lenient and mountpoint is None):
                        if not isinstance(mountpoint, str):
                            raise ValueError(
                                "Mountpoint {path} contains non-ASCII "
                                "(unsupported).".format(
                                    path=mountpoint
                                )
                            )
                        if not os.path.isabs(mountpoint):
                            raise ValueError(
                                "Mountpoint {path} is not an absolute path."
                                .format(
                                    path=mountpoint
                                )
                            )
                        configured_volume.pop('mountpoint')
                        if configured_volume:
                            raise ValueError(
                                "Unrecognised keys: {keys}.".format(
                                    keys=', '.join(sorted(
                                        configured_volume.keys()))
                                ))
                        mountpoint = FilePath(mountpoint)

                    volume = AttachedVolume(
                        name=application_name,
                        mountpoint=mountpoint
                        )
                except ValueError as e:
                    raise ConfigurationError(
                        ("Application '{application_name}' has a config "
                         "error. Invalid volume specification. {message}")
                        .format(
                            application_name=application_name,
                            message=e.message
                        )
                    )

            environment = self._parse_environment_config(
                application_name, config)

            applications[application_name] = Application(
                name=application_name,
                image=image,
                volume=volume,
                ports=frozenset(ports),
                links=links,
                environment=environment)

            if config:
                raise ConfigurationError(
                    ("Application '{application_name}' has a config error. "
                     "Unrecognised keys: {keys}.").format(
                        application_name=application_name,
                        keys=', '.join(sorted(config.keys())))
                )
        return applications

    def _applications_from_configuration(self, application_configuration):
        """
        Validate a given application configuration as either fig or flocker
        format and parse appropriately.

        :param dict application_configuration: The intermediate configuration
            representation to load into ``Application`` instances.  See
            :ref:`Configuration` for details.

        :raises ConfigurationError: if the config does not validate as either
            flocker or fig format.

        :returns: A ``dict`` mapping application names to ``Application``
            instances.
        """
        fig = FigConfiguration(application_configuration)
        if fig.is_valid_format():
            return fig.applications()
        else:
            return self._applications_from_flocker_configuration(
                application_configuration)

    def _deployment_from_configuration(self, deployment_configuration,
                                       all_applications):
        """
        Validate and parse a given deployment configuration.

        :param dict deployment_configuration: The intermediate configuration
            representation to load into ``Node`` instances.  See
            :ref:`Configuration` for details.

        :param set all_applications: All applications which should be running
            on all nodes.

        :raises ConfigurationError: if there are validation errors.

        :returns: A ``set`` of ``Node`` instances.
        """
        if 'nodes' not in deployment_configuration:
            raise ConfigurationError("Deployment configuration has an error. "
                                     "Missing 'nodes' key.")

        if u'version' not in deployment_configuration:
            raise ConfigurationError("Deployment configuration has an error. "
                                     "Missing 'version' key.")

        if deployment_configuration[u'version'] != 1:
            raise ConfigurationError("Deployment configuration has an error. "
                                     "Incorrect version specified.")

        nodes = []
        for hostname, application_names in (
                deployment_configuration['nodes'].items()):
            if not isinstance(application_names, list):
                raise ConfigurationError(
                    "Node {node_name} has a config error. "
                    "Wrong value type: {value_type}. "
                    "Should be list.".format(
                        node_name=hostname,
                        value_type=application_names.__class__.__name__)
                )
            node_applications = []
            for name in application_names:
                application = all_applications.get(name)
                if application is None:
                    raise ConfigurationError(
                        "Node {hostname} has a config error. "
                        "Unrecognised application name: "
                        "{application_name}.".format(
                            hostname=hostname, application_name=name)
                    )
                node_applications.append(application)
            node = Node(hostname=hostname,
                        applications=frozenset(node_applications))
            nodes.append(node)
        return set(nodes)

    def model_from_configuration(self, application_configuration,
                                 deployment_configuration):
        """
        Validate and coerce the supplied application configuration and
        deployment configuration dictionaries into a ``Deployment`` instance.

        :param dict application_configuration: Map of applications to Docker
            images.

        :param dict deployment_configuration: Map of node names to application
            names.

        :raises ConfigurationError: if there are validation errors.

        :returns: A ``Deployment`` object.
        """
        applications = self._applications_from_configuration(
            application_configuration)
        nodes = self._deployment_from_configuration(
            deployment_configuration, applications)
        return Deployment(nodes=frozenset(nodes))


model_from_configuration = Configuration().model_from_configuration


def current_from_configuration(current_configuration):
    """
    Validate and coerce the supplied current cluster configuration into a
    ``Deployment`` instance.

    The passed in configuration is the aggregated output of
    ``configuration_to_yaml`` as combined by ``flocker-deploy``.

    :param dict current_configuration: Map of node names to list of
        application maps.

    :raises ConfigurationError: if there are validation errors.

    :returns: A ``Deployment`` object.
    """
    configuration = Configuration(lenient=True)
    nodes = []
    for hostname, applications in current_configuration.items():
        node_applications = configuration._applications_from_configuration(
            applications)
        nodes.append(Node(hostname=hostname,
                          applications=frozenset(node_applications.values())))
    return Deployment(nodes=frozenset(nodes))


def configuration_to_yaml(applications):
    """
    Generate YAML representation of a node's applications.

    A bunch of information is missing, but this is sufficient for the
    initial requirement of determining what to do about volumes when
    applying configuration changes.
    https://github.com/ClusterHQ/flocker/issues/289

    :param applications: ``list`` of ``Application``\ s, typically the
        current configuration on a node as determined by
        ``Deployer.discover_node_configuration()``.

    :return: YAML serialized configuration in the application
        configuration format.
    """
    result = {}
    for application in applications:
        # XXX image unknown, see
        # https://github.com/ClusterHQ/flocker/issues/207
        result[application.name] = {"image": "unknown"}

        ports = []
        for port in application.ports:
            ports.append(
                {'internal': port.internal_port,
                 'external': port.external_port}
            )
        result[application.name]["ports"] = ports

        if application.links:
            links = []
            for link in application.links:
                links.append({
                    'local_port': link.local_port,
                    'remote_port': link.remote_port,
                    'alias': link.alias,
                    })
            result[application.name]["links"] = links

        if application.volume:
            # Until multiple volumes are supported, assume volume name
            # matches application name, see:
            # https://github.com/ClusterHQ/flocker/issues/49
            result[application.name]["volume"] = {
                "mountpoint": None,
            }
    return yaml.safe_dump({"version": 1, "applications": result})
