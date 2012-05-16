""":mod:`sider.list` --- List objects
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. seealso::

    `Redis Data Types <http://redis.io/topics/data-types>`_
       The Redis documentation that explains about its data
       types: strings, lists, sets, sorted sets and hashes.

"""
from __future__ import absolute_import
import collections
import numbers
import warnings
from redis.exceptions import ResponseError
from .types import Bulk, ByteString
from .session import Session
from .transaction import manipulative, query
from .warnings import PerformanceWarning


class List(collections.MutableSequence):
    """The Python-side representaion of Redis list value.  It behaves
    alike built-in Python :class:`list` object.  More exactly, it
    implements :class:`collections.MutableSequence` protocol.

    .. table:: Mappings of Redis commands--:class:`List` methods

       =================== ===========================================
       Redis commands      :class:`List` methods
       =================== ===========================================
       :redis:`LLEN`       :func:`len()` (:meth:`List.__len__()`)
       :redis:`LPUSH`      :meth:`List.insert()`
       :redis:`LPUSHX`     N/A
       :redis:`LPOP`       :meth:`List.pop()`
       :redis:`RPUSH`      :meth:`List.append()`,
                           :meth:`List.extend()`
       :redis:`RPUSHX`     N/A
       :redis:`RPOP`       :meth:`List.pop()`
       :redis:`RPOPLPUSH`  N/A
       :redis:`LINDEX`     :meth:`List.__getitem__()`,
       :redis:`LINSERT`    N/A
       :redis:`LRANGE`     :func:`iter()` (:meth:`List.__iter__()`),
                           :meth:`List.__getitem__()`,
       :redis:`LREM`       N/A
       :redis:`LTRIM`      :keyword:`del` (:meth:`List.__delitem__()`)
       :redis:`DEL`        :keyword:`del` (:meth:`List.__delitem__()`)
       :redis:`LSET`       :token:`=` (:meth:`List.__setitem__()`)
       :redis:`BLPOP`      N/A
       :redis:`BRPOP`      N/A
       :redis:`BRPOPLPUSH` N/A
       =================== ===========================================

    .. todo::

       Methods automatically filled by :class:`collections.MutableSequence`
       should warn :exc:`~sider.list.PerformanceWarning`.

    """

    #: (:class:`sider.types.Bulk`) The type of list values.
    value_type = None

    def __init__(self, session, key, value_type=ByteString):
        if not isinstance(session, Session):
            raise TypeError('session must be a sider.session.Session '
                            'instance, not ' + repr(session))
        self.session = session
        self.key = key
        self.value_type = Bulk.ensure_value_type(value_type,
                                                 parameter='value_type')

    def __iter__(self):
        i = 0
        step = 100  # FIXME: it is an arbitarary magic number.
        chunk = None
        decode = self.value_type.decode
        mark = self.session.mark_query
        while chunk is None or len(chunk) >= step:
            mark([self.key])
            mark = lambda *_: _
            chunk = self.session.client.lrange(self.key, i, i + step)
            for val in chunk:
                yield decode(val)

    @query
    def __len__(self):
        """Gets the number of the list elements.

        Use this with the built-in :func:`len()` function.

        :returns: the number of the list elements
        :rtype: :class:`numbers.Integral`

        .. note::

           This method is directly mapped to :redis:`LLEN`
           command.

        """
        return self.session.client.llen(self.key)

    def __getitem__(self, index):
        """Gets or slices the element of the given ``index``.

        :param index: the index of the element to get,
                      or the slice of a range to get
        :type index: :class:`numbers.Integral`, :class:`slice`
        :returns: the element value, or the sliced new list
        :raises exceptions.TypeError:
           when ``index`` is not an integer nor a slice of integers
        :raises exceptions.IndexError:
           when ``index`` is out of range

        .. note::

           This is mapped to :redis:`LINDEX` for integer indices and
           :redis:`LRANGE` for slices.

        """
        decode = self.value_type.decode
        if isinstance(index, numbers.Integral):
            self.session.mark_query([self.key])
            result = self.session.client.lindex(self.key, index)
            if result is None:
                raise IndexError(index)
            return decode(result)
        elif isinstance(index, slice):
            start = 0 if index.start is None else index.start
            stop = (0 if index.stop is None else index.stop) - 1
            self.session.mark_query([self.key])
            result = self.session.client.lrange(self.key, start, stop)
            if index.step is not None:
                result = result[::index.step]
            return map(decode, result)
        raise TypeError('indices must be integers, not ' + repr(index))

    def __setitem__(self, index, value):
        encode = self.value_type.encode
        if isinstance(index, numbers.Integral):
            value = encode(value)
            if self.session.current_transaction is None:
                try:
                    self.session.client.lset(self.key, index, value)
                except ResponseError:
                    raise IndexError(index)
            else:
                self.session.mark_query([self.key])
                length = len(self)
                if 0 <= index < length or -length <= index < 0:
                    self.session.mark_manipulative()
                    self.session.client.lset(self.key, index, value)
                else:
                    raise IndexError(index)
        elif isinstance(index, slice):
            if index.step is not None:
                raise ValueError('slice with step is not supported for '
                                 'assignment')
            elif index.start in (0, None) and index.stop == 1:
                seq = map(encode, value)
                seq.reverse()
                self.session.mark_manipulative([self.key])
                if self.session.server_version_info < (2, 4, 0):
                    pipe = self.session.client
                    if self.session.current_transaction is None:
                        pipe = pipe.pipeline()
                    key = self.key
                    for v in seq:
                        pipe.lpush(key, v)
                    if self.session.current_transaction is None:
                        pipe.execute()
                else:
                    self.session.client.lpush(self.key, *seq)
            else:
                cls = type(self)
                warnings.warn(
                    'assignment into slice of {0}.{1} may cause performance '
                    'issues'.format(cls.__module__, cls.__name__),
                    category=PerformanceWarning, stacklevel=2
                )
                def block(trial, transaction):
                    pipe = self.session.client
                    self.session.mark_query()
                    list_ = pipe.lrange(self.key, 0, -1)
                    list_[index] = map(encode, value)
                    self.session.mark_manipulative()
                    pipe.delete(self.key)
                    self._raw_extend(list_, pipe, encoded=True)
                self.session.transaction(block, [self.key], ignore_double=True)
        else:
            raise TypeError('indices must be integers, not ' + repr(index))

    def __delitem__(self, index):
        if isinstance(index, slice):
            if index.step is not None:
                raise ValueError('slice with step is not supported for '
                                 'deletion')
            if index.start is index.stop is None:
                self.session.mark_manipulative([self.key])
                self.session.client.delete(self.key)
            elif not all(v is None or isinstance(v, numbers.Integral)
                         for v in (index.start, index.stop)):
                raise TypeError('slice indices must be integers or None')
            elif index.start is None:
                self.session.mark_manipulative([self.key])
                self.session.client.ltrim(self.key, index.stop, -1)
            elif index.stop is None:
                self.session.mark_manipulative([self.key])
                self.session.client.ltrim(self.key, 0, index.start - 1)
            else:
                cls = type(self)
                warnings.warn(
                    'deleting middle of {0}.{1} may cause performance '
                    'issues'.format(cls.__module__, cls.__name__),
                    category=PerformanceWarning, stacklevel=2
                )
                def block(trial, transaction):
                    pipe = self.session.client
                    self.session.mark_query([self.key])
                    tail = pipe.lrange(self.key, index.stop, -1)
                    self.session.mark_manipulative()
                    pipe.ltrim(self.key, 0, index.start - 1)
                    self._raw_extend(tail, pipe, encoded=True)
                self.session.transaction(block, [self.key], ignore_double=True)
        elif isinstance(index, numbers.Integral):
            self.pop(index, _stacklevel=2)
        else:
            raise TypeError('index must be an integer')

    @manipulative
    def append(self, value):
        """Inserts the ``value`` at the tail of the list.

        :param value: an value to insert

        """
        data = self.value_type.encode(value)
        self.session.client.rpush(self.key, data)

    def extend(self, iterable):
        """Extends the list with the ``iterable``.

        :param iterable: an iterable object that extend the list with
        :type iterable: :class:`collections.Iterable`
        :raises exceptions.TypeError:
           if the given ``iterable`` is not iterable

        .. warning::

           Appending multiple values is supported since Redis 2.4.0.
           This may send :redis:`RPUSH` multiple times in a transaction
           if Redis version is not 2.4.0 nor higher.

        """
        pipe = self.session.client
        if self.session.current_transaction is None:
            pipe = pipe.pipeline()
        self._raw_extend(iterable, pipe)
        if self.session.current_transaction is None:
            pipe.execute()

    @manipulative
    def _raw_extend(self, iterable, pipe, encoded=False):
        encode = self.value_type.encode
        if self.session.server_version_info < (2, 4, 0):
            if encoded:
                for val in iterable:
                    pipe.rpush(self.key, val)
            else:
                for val in iterable:
                    pipe.rpush(self.key, encode(val))
        else:
            if not encoded:
                iterable = (encode(v) for v in iterable)
            pipe.rpush(self.key, *iterable)

    def insert(self, index, value):
        """Inserts the ``value`` right after the offset ``index``.

        :param index: the offset of the next before the place
                      where ``value`` would be inserted to
        :type index: :class:`numbers.Integral`
        :param value: the value to insert
        :raises exceptions.TypeError:
           if the given ``index`` is not an integer

        .. warning::

           Redis does not provide any primitive operations for random
           insertion.  You only can prepend or append a value into lists.
           If index is 0 it'll send :redis:`LPUSH` command, but otherwise
           it inefficiently :redis:`LRANGE` the whole list to manipulate
           it in offline, and then :redis:`DEL` the key so that empty
           the whole list, and then :redis:`RPUSH` the whole result again.
           Moreover all the commands execute in a transaction.

           So you should not treat this method as the same method of
           Python built-in :class:`list` object.  It is just for being
           compatible to :class:`collections.MutableSequence` protocol.

           If it faced the case, it also will warn you
           :class:`~sider.warnings.PerformanceWarning`.

        """
        if not isinstance(index, numbers.Integral):
            raise TypeError('index must be an integer, not ' + repr(index))
        data = self.value_type.encode(value)
        if index == 0:
            self.session.mark_manipulative([self.key])
            self.session.client.lpush(self.key, data)
        else:
            cls = type(self)
            warnings.warn(
                '{0}.{1}.insert() may cause performance issues when index is '
                'not 0 nor -1'.format(cls.__module__, cls.__name__),
                category=PerformanceWarning, stacklevel=2
            )
            def block(trial, transaction):
                pipe = self.session.client
                self.session.mark_query()
                list_ = pipe.lrange(self.key, 0, -1)
                list_.insert(index, data)
                self.session.mark_manipulative()
                pipe.delete(self.key)
                self._raw_extend(list_, pipe, encoded=True)
            self.session.transaction(block, [self.key], ignore_double=True)

    def pop(self, index=-1, _stacklevel=1):
        """Removes and returns an item at ``index``.  Negative index means
        ``len(list) + index`` (counts from the last).

        :param index: an index of an item to remove and return
        :type index: :class:`numbers.Integral`
        :param _stacklevel: internal use only. base stacklevel for warning.
                            default is 1
        :type _stacklevel: :class:`numbers.Integral`
        :returns: the removed element
        :raises exceptions.IndexError:
           if the given ``index`` doesn't exist
        :raises exceptions.TypeError:
           if the given ``index`` is not an integer

        .. warning::

           Redis doesn't offer any primitive operations for random deletion.
           You can pop only the last or the first.  Other middle elements
           cannot be popped in a command, so it emulates the operation
           inefficiently.

           Internal emulation routine to pop an other index than -1 or 0
           consists of three commands in a transaction:

           - :redis:`LINDEX`
           - :redis:`LTRIM`
           - :redis:`RPUSH` (In worst case, this command would be sent
             N times where N is the cardinality of elements placed after
             popped index.  Because multiple operands for :redis:`RPUSH`
             was supported since Redis 2.4.0.)

           So you should not treat this method as the same method of
           Python built-in :class:`list` object.  It is just for being
           compatible to :class:`collections.MutableSequence` protocol.

           If it faced the case, it also will warn you
           :class:`~sider.warnings.PerformanceWarning`.

        """
        client = self.session.client
        if not isinstance(index, numbers.Integral):
            raise TypeError('index must be an integer, not ' + repr(index))
        elif index == 0:
            if self.session.current_transaction is None:
                popped = client.lpop(self.key)
            else:
                self.session.mark_query([self.key])
                popped = client.lindex(self.key, 0)
                self.session.mark_manipulative()
                client.ltrim(self.key, 1, -1)
        elif index == -1:
            if self.session.current_transaction is None:
                popped = client.rpop(self.key)
            else:
                self.session.mark_query([self.key])
                popped = client.lindex(self.key, -1)
                self.session.mark_manipulative()
                client.ltrim(self.key, 0, -2)
        else:
            cls = type(self)
            warnings.warn(
                '{0}.{1}.pop() may cause performance issues when index '
                'is not 0 nor -1'.format(cls.__module__, cls.__name__),
                category=PerformanceWarning, stacklevel=_stacklevel + 1
            )
            result = [None]
            def block(trial, transaction):
                pipe = self.session.client
                self.session.mark_query([self.key])
                popped = pipe.lindex(self.key, index)
                if popped is None:
                    raise IndexError(index)
                result[0] = popped
                tail = pipe.lrange(self.key, index + 1, -1)
                self.session.mark_manipulative()
                pipe.ltrim(self.key, 0, index - 1)
                self._raw_extend(tail, pipe, encoded=True)
            self.session.transaction(block, [self.key], ignore_double=True)
            popped = result[0]
        if popped is None:
            raise IndexError(index)
        return self.value_type.decode(popped)

    @query
    def __repr__(self):
        def get_50():
            values = self.session.client.lrange(self.key, 0, 20)
            length = len(values)
            decode = self.value_type.decode
            for i, value in enumerate(values):
                if i < 20 or i == 20 and length <= 20:
                    yield repr(decode(value))
                else:
                    yield '...'
        cls = type(self)
        elements = ', '.join(get_50())
        return '<{0}.{1} ({2!r}) [{3}]>'.format(cls.__module__, cls.__name__,
                                                self.key, elements)

