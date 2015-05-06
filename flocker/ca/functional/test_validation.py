"""
Test validation of keys generated by flocker-ca.
"""

from tempfile import mkdtemp
from shutil import rmtree
import atexit

from pyrsistent import PRecord, field

from OpenSSL.SSL import Error as SSLError

from twisted.trial.unittest import TestCase
from twisted.internet.ssl import PrivateCertificate
from twisted.internet.endpoints import (
    SSL4ServerEndpoint, connectProtocol, SSL4ClientEndpoint,
    )
from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.python.filepath import FilePath
from twisted.internet.protocol import Protocol, ServerFactory


from ...testtools import find_free_port
from .._ca import (
    RootCredential, UserCredential, NodeCredential, ControlCredential)
from .._validation import ControlServicePolicy


EXPECTED_STRING = b"Mr. Watson, come here; I want to see you."


class SendingProtocol(Protocol):
    """
    Send a string.
    """
    def connectionMade(self):
        self.transport.write(EXPECTED_STRING)
        self.transport.loseConnection()


class ReceivingProtocol(Protocol):
    """
    Expect a string.

    :ivar Deferred result: Fires on receiving response or if disconnected
         before that.
    """
    def __init__(self):
        self.result = Deferred()
        self._buffer = b""

    def dataReceived(self, data):
        self._buffer += data
        if self._buffer == EXPECTED_STRING:
            self.result.callback(None)
            self.result = None

    def connectionLost(self, reason):
        if self.result:
            self.result.errback(reason)
            self.result = None


class CredentialSet(PRecord):
    """
    A full set of credentials for a CA.

    :param RootCredential root: The CA root credential.
    :param ControlCredential control: A control service credential.
    :param UserCredential user: A user credential.
    :param NodeCredential node: A CA root credentials.
    """
    root = field()
    control = field()
    user = field()
    node = field()

    @staticmethod
    def create():
        """
        :return: A new ``CredentialSet``.
        """
        directory = FilePath(mkdtemp())
        atexit.register(rmtree, directory.path)
        root = RootCredential.initialize(directory, b"mycluster")
        user = UserCredential.initialize(directory, root, u"allison")
        node = NodeCredential.initialize(directory, root)
        control = ControlCredential.initialize(directory, root, b"127.0.0.1")
        return CredentialSet(root=root, user=user, node=node, control=control)


def get_sets():
    """
    :return: Tuple, a pair of ``CredentialSet`` instances.
    """
    a = CredentialSet.create()
    b = CredentialSet.create()
    global get_sets

    def get_sets():
        return a, b
    return get_sets()


def make_validation_tests(validating_client_endpoint_fixture,
                          good_certificate_name):
    """
    Create a ``TestCase`` for the validator of a specific certificate type.

    :param validating_client_endpoint_fixture: Create a client endpoint
         that implements the required validation given a
         ``CredentialSet``, given a port number on localhost to connect to
         and a ``CertificateSet``.

    :param str good_certificate_name: Name of certificate (an attribute of
        ``CredentialSet``) that should validate successfully.

    :return TestCase: Tests for given validator.
    """
    bad_name, another_bad_name = {"user", "node", "control"}.difference(
        {good_certificate_name})

    class ValidationTests(TestCase):
        """
        Tests to ensure correct validation of a specific type of certificate.

        :ivar CertificateSet good_ca: The certificates for the CA we expect.

        :ivar CertificateSet another_ca: A different CA's certificates.
        """
        def setUp(self):
            self.good_ca, self.another_ca = get_sets()

        def assert_validates(self, credential):
            """
            Asserts that a TLS handshake is successfully established between a
            client using the validation logic and a server based on the
            given credential.

            :param credential: The high-level credential to use.

            :return ``Deferred``: Fires on success.
            """
            credential = credential.credential
            private_certificate = PrivateCertificate.fromCertificateAndKeyPair(
                credential.certificate, credential.keypair.keypair)
            port = find_free_port()[1]
            server_endpoint = SSL4ServerEndpoint(reactor, port,
                                                 private_certificate.options(),
                                                 interface='127.0.0.1')
            d = server_endpoint.listen(
                ServerFactory.forProtocol(SendingProtocol))
            d.addCallback(lambda port: self.addCleanup(port.stopListening))
            validating_endpoint = validating_client_endpoint_fixture(
                port, self.good_ca)
            client_protocol = ReceivingProtocol()
            result = connectProtocol(validating_endpoint, client_protocol)
            result.addCallback(lambda _: client_protocol.result)
            return result

        def assert_does_not_validate(self, credential):
            """
            Asserts that a TLS handshake fails to happen between a client using
            the validation logic and a server based on the given
            credential.

            :param FlockerCredential credential: The private key/certificate
                to use for the server.

            :return ``Deferred``: Fires on success (i.e. if no TLS handshake is
                established).
            """
            return self.assertFailure(self.assert_validates(credential),
                                      SSLError)

        def test_same_ca_correct_type(self):
            """
            If the expected certificate type is generated by the same CA
            then the validator will successfully validate it.
            """
            return self.assert_validates(
                getattr(self.good_ca, good_certificate_name))

        def test_different_ca_correct_type(self):
            """
            If the expected certificate type is generated by a different
            CA then the validator will reject it.
            """
            return self.assert_does_not_validate(
                getattr(self.another_ca, good_certificate_name))

        def test_same_ca_wrong_type(self):
            """
            If the expected certificate is generated by the same CA but is of
            the wrong type the validator will reject it.
            """
            return self.assert_does_not_validate(
                getattr(self.another_ca, bad_name))

        def test_same_ca_another_wrong_type(self):
            """
            If the expected certificate is generated by the same CA but is of
            the wrong type the validator will reject it.

            This is different wrong type than ``test_same_ca_wrong_type``.
            """
            return self.assert_does_not_validate(
                getattr(self.another_ca, another_bad_name))

    return ValidationTests


class ControlServicePolicyValidationTests(
        make_validation_tests(
            lambda port, good_ca: SSL4ClientEndpoint(
                reactor, b"127.0.0.1", port, ControlServicePolicy(
                    ca_certificate=good_ca.root.credential.certificate,
                    # This value is irrelevant to the test, but required:
                    client_certificate=PrivateCertificate.fromCertificateAndKeyPair(
                        good_ca.user.credential.certificate,
                        good_ca.user.credential.keypair.keypair)).creatorForNetloc(
                            b"127.0.0.1", port)),
            # We expect control certificate to validate correctly:
            "control")):
    """
    Tests for validation of the control service certificate by clients.
    """


