""":mod:`sider.session` --- Sessions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

What sessions mainly do are `identity map`__ and `unit of work`__.

__ http://martinfowler.com/eaaCatalog/identityMap.html
__ http://martinfowler.com/eaaCatalog/unitOfWork.html

"""
from __future__ import absolute_import
import warnings
from redis.client import StrictRedis, Redis
from .threadlocal import LocalDict
from .types import Value, ByteString
from .transaction import Transaction
from .exceptions import CommitError


class Session(object):
    """Session is an object which manages Python objects that represent Redis
    values e.g. lists, sets, hashes.  It maintains identity maps between
    Redis values and Python objects, and deals with transactions.

    :param client: the Redis client
    :type client: :class:`redis.client.StrictRedis`

    """

    #: (:class:`bool`) If it is set to ``True``, error messages raised by
    #: transactions will contain tracebacks where they started query/commit
    #: phase.
    #:
    #: It is mostly for debug purpose, and you can set this to ``True``
    #: if it's needed.
    verbose_transaction_error = None

    def __init__(self, client):
        if not isinstance(client, StrictRedis):
            raise TypeError('client must be a redis.client.StrictRedis object'
                            ', not ' + repr(client))
        elif isinstance(client, Redis):
            warnings.warn('redis.client.Redis is deprecated and would be '
                          'broken in the future; use redis.client.StrictRedis '
                          'instead', DeprecationWarning)
        self.client = client
        self.basic_client = client
        self.context_locals = LocalDict(transaction=None)
        self.verbose_transaction_error = False

    @property
    def server_version(self):
        """(:class:`str`) Redis server version string e.g. ``'2.2.11'``."""
        try:
            info = self._server_info
        except AttributeError:
            info = self.client.info()
            self._server_info = info
        return info['redis_version']

    @property
    def server_version_info(self):
        """(:class:`tuple`) Redis server version triple e.g. ``(2, 2, 11)``.
        You can compare versions using this property.

        """
        return tuple(int(v) for v in self.server_version.split('.'))

    def get(self, key, value_type=ByteString):
        """Loads the value from the ``key``.
        If ``value_type`` is present the value will be treated as it,
        or :class:`~sider.types.ByteString` by default.

        :param key: the Redis key to load
        :type key: :class:`str`
        :param value_type: the type of the value to load.  default is
                           :class:`~sider.types.ByteString`
        :type value_type: :class:`~sider.types.Value`, :class:`type`
        :returns: the loaded value

        """
        value_type = Value.ensure_value_type(value_type,
                                             parameter='value_type')
        return value_type.load_value(self, key)

    def set(self, key, value, value_type=ByteString):
        """Stores the ``value`` into the ``key``.
        If ``value_type`` is present the value will be treated as it,
        or :class:`~sider.types.ByteString` by default.

        :param key: the Redis key to save the value into
        :type key: :class:`str`
        :param value: the value to be saved
        :param value_type: the type of the ``value``.  default is
                           :class:`~sider.types.ByteString`
        :type value_type: :class:`~sider.types.Value`, :class:`type`
        :returns: the Python representation of the saved value.
                  it is equivalent to the given ``value`` but
                  may not equal nor the same to

        """
        value_type = Value.ensure_value_type(value_type,
                                             parameter='value_type')
        return value_type.save_value(self, key, value)

    @property
    def current_transaction(self):
        """(:class:`~sider.transaction.Transaction`) The current transaction.
        It could be ``None`` when it's not on any transaction.

        """
        return self.context_locals['transaction']

    @property
    def transaction(self):
        """(:class:`sider.transaction.Transaction`) The transaction object
        for the session.

        :class:`~sider.transaction.Transaction` objects are callable and so
        you can use this :attr:`transaction` property as like a method::

            def block(trial, transaction):
                list_[0] = list[0].upper()
            session.transaction(block)

        Or you can use it in a :keyword:`for` loop::

            for trial in session.transaction:
                list_[0] = list[0].upper()

        .. seealso::

           Method :meth:`sider.transaction.Transaction.__call__()`
              Executes a given block in the transaction.

           Method :meth:`sider.transaction.Transaction.__iter__()`
              More explicit way to execute a routine in
              the transaction than :meth:`Transaction.__call__()
              <sider.transaction.Transaction.__call__>`

        """
        return Transaction(self)

    def mark_manipulative(self, keys=frozenset()):
        """Marks it is manipulative.

        :param keys: optional set of keys to watch
        :type keys: :class:`collections.Iterable`

        .. note::

           This method is for internal use.

        """
        transaction = self.current_transaction
        if transaction is None:
            return
        transaction.watch(keys)
        if not transaction.commit_phase:
            transaction.begin_commit()

    def mark_query(self, keys=frozenset()):
        """Marks it is querying.

        :param keys: optional set of keys to watch
        :type keys: :class:`collections.Iterable`

        :raises sider.exceptions.CommitError:
           when it is tried during commit phase

        .. note::

           This method is for internal use.

        """
        transaction = self.current_transaction
        if transaction is None:
            return
        if transaction.commit_phase:
            raise CommitError('query operation was tried during commit phase' +
                              transaction.format_commit_stack())
        else:
            transaction.watch(keys)

