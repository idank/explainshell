# -*- coding: utf-8 -*-
#
# Copyright (C) 2012-2013 Vinay M. Sajip. See LICENSE for licensing information.
#
# sarge: Subprocess Allegedly Rewards Good Encapsulation :-)
#
from io import BytesIO
import logging
import os

try:
    import queue
except ImportError:     #pragma: no cover
    import Queue as queue
import re
import shutil
import signal
import string
import subprocess
import sys
import threading

from .shlext import shell_shlex

__all__ = ('shell_quote', 'Capture', 'Command', 'ShellFormatter', 'Pipeline',
           'shell_format', 'run', 'parse_command_line',
           'capture_stdout', 'capture_stderr', 'capture_both')

__version__ = '0.1.2.dev0'

logger = logging.getLogger(__name__)

# We use a separate logger for parsing, as that's sometimes too much
# information :-)
parse_logger = logging.getLogger('%s.parse' % __name__)
#
# This runs on Python 2.x and 3.x from the same code base - no need for 2to3.
#
if sys.version_info[0] < 3:
    PY3 = False
    text_type = unicode
    binary_type = str
    string_types = basestring,
else:
    PY3 = True
    text_type = str
    binary_type = bytes
    string_types = str,
    basestring = str

# This regex determines which shell input needs quoting
# because it may be unsafe
UNSAFE = re.compile(r'[^\w%+,./:=@-]')

def shell_quote(s):
    """
    Quote text so that it is safe for Posix command shells.

    For example, "*.py" would be converted to "'*.py'". If the text is
    considered safe it is returned unquoted.

    :param s: The value to quote
    :type s: str (or unicode on 2.x)
    :return: A safe version of the input, from the point of view of Posix
             command shells
    :rtype: The passed-in type
    """
    assert isinstance(s, string_types)
    if not s:
        result = "''"
    elif len(s) >= 2 and (s[0], s[-1]) == ("'", "'"):
        result = '"%s"' % s.replace('"', r'\"')
    elif not UNSAFE.search(s):
        result = s
    else:
        result = "'{0}'".format(s.replace("'", "'\"'\"'"))
    return result


class ShellFormatter(string.Formatter):
    """
    This class overrides :class:`string.Formatter` to provide a custom
    :meth:`convert_field` method, which ensures that fields are quoted for
    safety using :func:`shell_quote`.
    """

    def convert_field(self, value, conversion):
        """
        Convert a field to text.

        If a conversion is specified (e.g. !s, !r), no quoting is performed.
        If *no* conversion is specified, the value is converted to string
        (using :func:`str`) and that value is quoted using :func:`shell_quote`
        before being returned.
        :param value: The value to be converted
        :type value: any
        :param conversion: The conversion to apply
        :type conversion: str (or None)
        :return: The converted value
        :rtype: str
        """
        if conversion is None:
            result = shell_quote(str(value))
        else:
            result = super(ShellFormatter, self).convert_field(value,
                                                               conversion)
        return result

def shell_format(fmt, *args, **kwargs):
    """
    Format a shell command with format placeholders and variables to fill
    those placeholders.

    Note: you must specify positional parameters explicitly, i.e. as {0}, {1}
    instead of {}, {}. Requiring the formatter to maintain its own counter can
    lead to thread safety issues unless a thread local is used to maintain
    the counter. It's not that hard to specify the values explicitly
    yourself :-)

    :param fmt: The shell command as a format string. Note that you will need
                to double up braces you want in the result, i.e. { -> {{ and
                } -> }}, due to the way :meth:`str.format` works.
    :type fmt: str, or unicode on 2.x
    :param args: Positional arguments for use with ``fmt``.
    :param kwargs: Keyword arguments for use with ``fmt``.
    :return: The formatted shell command, which should be safe for use in
             shells from the point of view of shell injection.
    :rtype: The type of ``fmt``.
    """
    return ShellFormatter().vformat(fmt, args, kwargs)


class WithMixin(object):
    """
    This class provides a very simple mixin for objects which can be used
    in a ``with`` statement.
    """
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

default_capture_timeout = 0.02
default_expect_timeout = 5.0


class Capture(WithMixin):
    """
    This class encapsulates an output stream of a sub-process. You just set
    ``stdout`` or ``stderr`` of a :class:`Command` or :class:`Pipeline` to an
    instance of this class.

    :param timeout: The timeout to use for this instance. If not specified,
                    the module attribute ``default_capture_timeout` is used.
    :type timeout: float
    :param buffer_size: The buffer size to use when reading from streams.
                        If not specified, a 4K buffer is used.
    :type buffer_size: int
    """
    counter = 1

    # These are needed to allow wrapping using TextIOWrapper
    readable = lambda self: True
    writable = lambda self: False
    seekable = readable
    closed = False

    def __init__(self, timeout=None, buffer_size=-1, encoding='utf-8'):
        self.timeout = timeout or default_capture_timeout
        self.streams = []
        self.buffer = queue.Queue()
        self.buffer_size = buffer_size or 4096
        self.encoding = encoding
        self.current = None
        self._bytes = None
        self.threads = []
        self.pattern = None
        self.matched = threading.Event()
        self.match = None
        self.match_index = 0
        self.counter = self.__class__.counter
        self.__class__.counter += 1

    def add_stream(self, stream):
        """
        Add a stream to this instance. A new thread is spawned to read from
        the stream into the capture queue for this instance.

        :param stream: An output stream from a child process (i.e. the read
                       end of a pipe, whose write end is the output stream
                       from the process.
        """
        self.streams.append(stream)

        ready = threading.Event()
        t = threading.Thread(target=self.reader, args=(stream, ready))
        self.threads.append(t)
        t.daemon = True
        t.start()
        logger.debug('%r: reader thread kicked off, waiting start', self)
        ready.wait()
        logger.debug('%r: reader thread now started', self)

    def reader(self, stream, ready):
        """
        The callable used as the runnable in reader threads.

        :param stream: The stream to read.
        :param ready: A :class:`threading.Event` instance to set when the
                      reader thread starts executing.
        """
        ready.set()
        chunk_size = self.buffer_size
        if chunk_size > 0:
            logger.debug('%r: reader thread about to read %s', self,
                         chunk_size)
        else:
            logger.debug('%r: reader thread about to read line', self)
        self._done = False
        while not self._done:
            if chunk_size < 0:
                chunk = stream.readline()
            else:
                chunk = stream.read(chunk_size)
            if chunk:
                self.buffer.put_nowait(chunk)
                logger.debug('queued chunk of length %d: %r', len(chunk),
                             chunk[:30])
                if self.pattern and not self.matched.is_set():
                    self._try_match()
            if chunk_size > 0:
                if len(chunk) < chunk_size:
                    break
            else:
                if not chunk:
                    break
        logger.debug('%r: finished reading stream %s', self, stream)
        stream.close()

    @property
    def bytes(self):
        """
        All the bytes in the capture queue.
        """
        data = self.read()
        if self._bytes is not None:
            data = self._bytes + data
        self._bytes = data
        return data

    @property
    def text(self):
        """
        All the bytes in the capture queue, decoded as text.
        """
        return self.bytes.decode(self.encoding)

    def streams_open(self):
        result = False
        for c in self.streams:
            if not c.closed:
                result = True
                break
        return result

    def read(self, size=-1, block=True, timeout=None):
        if not self.streams_open():
            block = False
            timeout = None
        else:
            timeout = timeout or self.timeout
        if size <= 0:
            b = []
            while True:
                try:
                    b.append(self.buffer.get(block, timeout))
                except queue.Empty:
                    break
            result = b''.join(b)
        else:
            if self.current is None:
                try:
                    self.current = self.buffer.get(block, timeout)
                except queue.Empty:
                    self.current = b''
            while not self.current:
                try:
                    self.current += self.buffer.get(block, timeout)
                except queue.Empty:
                    break
            if len(self.current) <= size:
                result = self.current
                self.current = None
            else:
                result = self.current[:size]
                self.current = self.current[size:]
        return result

    def read1(self, n):
        return self.read(n)

    def readline(self, size=-1, block=True, timeout=None):
        if not self.streams_open():
            block = False
            timeout = None
        else:
            timeout = timeout or self.timeout
        if self.current is None:
            try:
                self.current = self.buffer.get(block, timeout)
            except queue.Empty:
                self.current = b''
        while b'\n' not in self.current:
            try:
                self.current += self.buffer.get(block, timeout)
            except queue.Empty:
                break
        if b'\n' not in self.current:
            result = self.current
            self.current = None
        else:
            i = self.current.index(b'\n')
            if 0 < size < i:
                i = size - 1
            result = self.current[:i + 1]
            self.current = self.current[i + 1:]
        return result

    def readlines(self, sizehint=-1, block=True, timeout=None):
        if not self.streams_open():
            block = False
            timeout = None
        else:
            timeout = timeout or self.timeout
        data = self.read(sizehint, block, timeout)
        if self.current is not None:
            data = self.current + data
        self.current = None
        return data.splitlines(True)

    def _try_match(self):
        data = self.bytes
        if data and self.pattern:
            m = self.pattern.search(data, self.match_index)
            if m:
                logger.debug('Found at %s after %d bytes', m.span(), len(data))
                self.match = m
                self.match_index = m.end()
                self.matched.set()

    def expect(self, pattern, timeout=None):
        def as_pattern(p):
            if isinstance(p, string_types):
                if isinstance(p, text_type):
                    p = p.encode('utf-8')
                p = re.compile(p, re.MULTILINE)
            return p

        self.pattern = as_pattern(pattern)
        self.matched.clear()
        self.match = None
        self._try_match()
        if not self.match:
            if timeout is None:
                timeout = default_expect_timeout
            self.matched.wait(timeout)
        return self.match

    def __iter__(self):
        while True:
            line = self.readline()
            if not line:
                break
            yield line

    def close(self, stop_threads=False):
        if stop_threads:
            self._done = True   # may lose some data sent from subprocess
        for t in self.threads:
            try:
                t.join()
            except RuntimeError:    #pragma: no cover
                logger.debug('failed to join thread: %s', t)
                #raise

    def __repr__(self):
        return '%s-%d' % (self.__class__.__name__, self.counter)


class Feeder(object):
    """
    Facilitate sending data to a child process over time rather than
    just when the child is spawned.
    """
    def __init__(self):
        self._r, self._w = os.pipe()

    def fileno(self):
        return self._r

    def feed(self, data):
        if isinstance(data, text_type):
            data = data.encode('utf-8')
        if not isinstance(data, bytes):
            raise TypeError('Bytes expected, got %s' % type(data))
        os.write(self._w, data)

    def close(self):
        if self._r:
            os.close(self._r)
            self._r = None
        if self._w:
            os.close(self._w)
            self._w = None


def ensure_stream(input, encoding='utf-8'):
    """
    Convert a possible text value into a binary file-like object.
    """
    if isinstance(input, text_type):
        input = input.encode(encoding)   # need to be explicit for 2.x!
    if isinstance(input, binary_type):
        input = BytesIO(input)
        logger.debug('returning %s', input)
    return input

# Used to redirect stdout to stderr. Use a value less likely to clash with
# future additions to subprocess.py.
STDERR = -9

#
# A dummy redirects dict which indicates a desire to swap stdout and stderr
# in the child.
#
SWAP_OUTPUTS = {
    1: ('>', ('&', 2)),
    2: ('>', ('&', 3)),
    3: ('>', ('&', 1)),
    }

class Popen(subprocess.Popen):
    """
    This is a subclass of :class:`subprocess.Popen` which is there in case we
    need to provide specialised functionality for use in sarge. For example,
    we can't do >&2 redirection in subprocess.Popen, though we can do 2>&1
    """

    def _get_handles(self, stdin, stdout, stderr):
        def close(h):
            if h not in (-1, None):
                if subprocess.mswindows:
                    h.Close()
                else:
                    os.close(h)

        def dup(h):
            if subprocess.mswindows:
                result = self._make_inheritable(h)
            else:
                result = os.dup(h)
            return result

        if stdout == STDERR and stderr == subprocess.STDOUT:
            PIPE = subprocess.PIPE
            (p2cread, p2cwrite,
             c2pread, c2pwrite,
             errread, errwrite) = super(Popen, self)._get_handles(stdin, PIPE,
                                                                  PIPE)
            logger.debug('swapping stdout and stderr')
            return p2cread, p2cwrite, errread, errwrite, c2pread, c2pwrite
        else:
            orig_stdout = stdout
            if stdout == STDERR:
                stdout = None
            (p2cread, p2cwrite,
             c2pread, c2pwrite,
             errread, errwrite) = super(Popen, self)._get_handles(stdin,
                                                                  stdout,
                                                                  stderr)
            if orig_stdout == STDERR:
                # c2pread, c2pwrite are None on 2,x and -1 on 3,x
                close(c2pread)
                close(c2pwrite)
                c2pread = dup(errread)
                c2pwrite = dup(errwrite)
            return p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite

    if os.name == 'posix' and sys.version_info[0] < 3:
        # Issue 12: add restore_signals support to avoid spurious
        # output on broken pipes
        def _execute_child(self, args, executable, preexec_fn, *rest):
            # can only call signal.signal in the main thread
            if threading.current_thread().name != 'MainThread':
                preexec = preexec_fn
            else:
                def preexec():
                    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
                    if preexec_fn:
                        preexec_fn()
            super(Popen, self)._execute_child(args, executable, preexec, *rest)

    def __repr__(self):
        values = []
        for attr in ('returncode', 'stdin', 'stdout', 'stderr'):
            values.append('%s=%s' % (attr, getattr(self, attr, None)))
        return '%s(%s)' % (self.__class__.__name__, ' '.join(values))

def copier(src, dest):
    shutil.copyfileobj(src, dest)
    dest.close()


class Command(object):
    """
    This class represents a shell command to be run in a subprocess.

    :param args:   The command string or array or command/args to be run.
    :type args:    This is the same as the first argument to the constructor of
                   :class:`subprocess.Popen'.
    :param kwargs: The same as you would pass to :class:`subprocess.Popen'.
                   However, the ``env`` parameter is handled differently: it
                   is treated as *additional* environment variables to be
                   added to the values in ``os.environ``.
    """

    def __init__(self, args, **kwargs):
        shell = kwargs.get('shell')
        if not shell and isinstance(args, string_types):
            args = list(shell_shlex(args, control='();>|&'))
        self.args = args
        self.kwargs = kwargs
        # check for input specified
        if kwargs.get('stdin'):
            raise ValueError('Inputs need to be specified via the run method.')
        # special handling of Capture instances in stdout, stderr
        for attr in ('stdout', 'stderr'):
            s = kwargs.get(attr)
            if isinstance(s, Capture):
                kwargs[attr] = subprocess.PIPE
                setattr(self, attr, s)
        # special handling: env is added to os.environ
        e = kwargs.get('env')
        if e:
            env = dict(os.environ)
            env.update(e)
            kwargs['env'] = env
        self.process_ready = threading.Event()
        self.process = None
        logger.debug('%r created', self)

    def __repr__(self):
        if isinstance(self.args, basestring):
            s = self.args
        else:
            s = ' '.join(self.args)
        return '%s(%r)' % (self.__class__.__name__, s)

    def run(self, input=None, async=False):
        """
        Run the command with optional input and either synchronously or
        asynchronously.

        :param input: The input to pass to the command subprocess.
        :type input:  If this is text, it is encoded to bytes using UTF-8.
                      If it is a byte string, it is used as is. Otherwise, a
                      file-like object containing bytes should be passed: this
                      will be read to the end, but not closed.
        :param async: If ``True``, this method returns without waiting for the
                      subprocess to complete. Otherwise, it awaits completion
                      by calling the :meth:`subprocess.Popen.wait` method.
        :type async:  bool
        """
        #noinspection PyBroadException
        try:
            if input is None:
                self.kwargs['stdin'] = None
            else:
                input = ensure_stream(input)
                if not isinstance(input, BytesIO):
                    if hasattr(input, 'fileno'):
                        input = input.fileno()
                    self.kwargs['stdin'] = input
                else:
                    self.kwargs['stdin'] = subprocess.PIPE
            self.process = p = Popen(self.args, **self.kwargs)
            self.stdin = p.stdin
            logger.debug('Popen: %s, %s -> %s', self, self.kwargs, p.__dict__)
            if isinstance(input, BytesIO):
                t = threading.Thread(target=copier, args=(input, p.stdin))
                t.daemon = True
                t.start()
                # The thread may take a while to finish, but we want to wait
                # until it's on its way; otherwise, any pipeline dependent on
                # this input could be dead-locked.
                # Possibly a better mechanism than just waiting a while is needed.
                # I've tried a threading.Event passed in from here which gets
                # set after a chunk has been written in the copier and which we
                # wait for here, but that doesn't seem to work reliably.
                t.join(0.0001)
            for attr in ('stdout', 'stderr'):
                s = getattr(self, attr, None)
                if isinstance(s, Capture):
                    s.add_stream(getattr(p, attr))
            if not async:
                logger.debug('about to wait for process')
                p.wait()
        finally:
            self.process_ready.set()
        logger.debug('returning %s (%s)', self, self.process)
        return self

    def wait(self):
        """
        Wait for a command's underlying sub-process to complete.
        """
        self.process_ready.wait()
        p = self.process
        if not p:   #pragma: no cover
            logger.warning('No process found for %s', self)
            result = None
        else:
            result = p.wait()
        return result

    def terminate(self):
        """
        Terminate a command's underlying subprocess.

        .. versionadded:: 0.1.1
        """
        self.process_ready.wait()
        p = self.process
        if not p: #pragma: no cover
            raise ValueError('There is no subprocess')
        p.terminate()

    def kill(self):
        """
        Kill a command's underlying subprocess.

        .. versionadded:: 0.1.1
        """
        self.process_ready.wait()
        p = self.process
        if not p: #pragma: no cover
            raise ValueError('There is no subprocess')
        p.kill()

    def poll(self):
        """
        Poll a command's underlying subprocess.

        .. versionadded:: 0.1.1
        """
        self.process_ready.wait()
        p = self.process
        if not p: #pragma: no cover
            raise ValueError('There is no subprocess')
        return p.poll()

    @property
    def returncode(self):
        self.process_ready.wait()
        return self.process.returncode

class Node(object):
    """
    This class represents a node in the AST built while parsing command lines.
    It's basically an object container for various attributes, with a slightly
    specialised representation to make it a little easier to debug the parser.
    """

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __repr__(self):
        chunks = []
        d = dict(self.__dict__)
        kind = d.pop('kind')
        for k, v in sorted(d.items()):
            chunks.append('%s=%s' % (k, v))
        return '%sNode(%s)' % (kind.title(), ' '.join(chunks))


class CommandLineParser(object):
    """
    This class implements a fairly unsophisticated recursive descent parser for
    shell command lines as used in sh, bash and dash. On Windows, the cmd.exe
    command shell has limited compatibility
    """

    permitted_tokens = ('&&', '||', '|&', '>>')

    def next_token(self):
        t = self.lex.get_token()
        if not t:
            tt = None
        else:
            tt = self.lex.token_type
            if tt in ('"', "'"):
                tt = 'word'
                t = t[1:-1]
            elif tt == 'a':
                try:
                    int(t)
                    tt = 'number'
                except ValueError:
                    tt = 'word'
            elif tt == 'c':
                # the shlex parser will return arbitrary runs of 'control'
                # characters, but only some runs will be valid for us. We
                # split into the valid runs and push all the others back,
                # keeping just one. Since all will have a token_type of
                # 'c', we don't need to worry about pushing the type back.
                if len(t) > 1:
                    valid = self.get_valid_controls(t)
                    t = valid.pop(0)
                    if valid:
                        for other in reversed(valid):
                            self.lex.push_token(other)
                tt = t
        return tt, t, self.lex.preceding

    def get_valid_controls(self, t):
        if len(t) == 1:
            result = [t]
        else:
            result = []
            last = None
            for c in t:
                if last is not None:
                    combined = last + c
                    if combined in self.permitted_tokens:
                        result.append(combined)
                    else:
                        result.append(last)
                        result.append(c)
                    last = None
                elif c not in ('>', '&', '|'):
                    result.append(c)
                else:
                    last = c
            if last:
                result.append(last)
                #logger.debug('%s -> %s', t, result)
        return result

    def peek_token(self):
        if self.peek is None:
            self.peek = self.next_token()
        return self.peek[0]

    def consume(self, tt):
        self.token = self.peek
        self.peek = self.next_token()
        if self.token[0] != tt:
            raise ValueError('consume: expected %r', tt)

    def parse(self, source, posix=None):
        self.source = source
        parse_logger.debug('starting parse of %r', source)
        if posix is None:
            posix = os.name == 'posix'
        self.lex = shell_shlex(source, posix=posix, control=True)
        self.token = None
        self.peek = None
        self.peek_token()
        result = self.parse_list()
        return result

    def parse_list(self):
        parts = [self.parse_pipeline()]
        tt = self.peek_token()
        while tt in (';', '&'):
            self.consume(tt)
            part = self.parse_pipeline()
            parts.append(Node(kind='sync', sync=tt))
            parts.append(part)
            tt = self.peek_token()
        if len(parts) == 1:
            node = parts[0]
        else:
            node = Node(kind='list', parts=parts)
        parse_logger.debug('returning %r', node)
        return node

    def parse_pipeline(self):
        parts = [self.parse_logical()]
        tt = self.peek_token()
        while tt in ('&&', '||'):
            self.consume(tt)
            part = self.parse_logical()
            parts.append(Node(kind='check', check=tt))
            parts.append(part)
            tt = self.peek_token()
        if len(parts) == 1:
            node = parts[0]
        else:
            node = Node(kind='pipeline', parts=parts)
        parse_logger.debug('returning %r', node)
        return node

    def parse_logical(self):
        tt = self.peek_token()
        if tt == '(':
            self.consume(tt)
            node = self.parse_list()
            self.consume(')')
        else:
            parts = [self.parse_command()]
            tt = self.peek_token()
            while tt in ('|', '|&'):
                last_part = parts[-1]
                if ((tt == '|' and 1 in last_part.redirects) or
                    (tt == '|&' and 2 in last_part.redirects)):
                    if last_part.redirects != SWAP_OUTPUTS:
                        raise ValueError(
                            'semantics: cannot redirect and pipe the '
                            'same stream')
                self.consume(tt)
                part = self.parse_command()
                parts.append(Node(kind='pipe', pipe=tt))
                parts.append(part)
                tt = self.peek_token()
            if len(parts) == 1:
                node = parts[0]
            else:
                node = Node(kind='logical', parts=parts)
        parse_logger.debug('returning %r', node)
        return node

    def add_redirection(self, node, fd, kind, dest):
        if fd in node.redirects:
            raise ValueError('semantics: cannot redirect stream %d twice' % fd)
        node.redirects[fd] = (kind, dest)

    def parse_command(self):
        node = self.parse_command_part()
        tt = self.peek_token()
        while tt in ('word', 'number'):
            part = self.parse_command_part()
            node.command.extend(part.command)
            for fd, v in part.redirects.items():
                self.add_redirection(node, fd, v[0], v[1])
            tt = self.peek_token()
        parse_logger.debug('returning %r', node)
        if node.redirects != SWAP_OUTPUTS:
            d = dict(node.redirects)
            d.pop(1, None)
            d.pop(2, None)
            if d:
                raise ValueError('semantics: can only redirect stdout and '
                                 'stderr, not %s' % list(d.keys()))
        if sys.platform == 'win32':
            from .utils import find_command

            cmd = find_command(node.command[0])
            if cmd:
                exe, cmd = cmd
                node.command[0] = cmd
                if exe:
                    node.command.insert(0, exe)
        return node

    def parse_command_part(self):
        node = Node(kind='command', command=[self.peek[1]], redirects={})
        if self.peek[0] == 'word':
            self.consume('word')
        else:
            self.consume('number')
        tt = self.peek_token()
        while tt in ('>', '>>'):
            num = 1     # default value
            if self.peek[2] == '':
                # > or >> seen without preceding whitespace. So see if the
                # last token is a positive integer. If it is, assume it's
                # an fd to redirect and pop it, else leave it in as part of
                # the command line.
                try:
                    try_num = int(node.command[-1])
                    if try_num > 0:
                        num = try_num
                        node.command.pop()
                except ValueError:
                    pass
            redirect_kind = tt
            self.consume(tt)
            tt = self.peek_token()
            if tt not in ('word', '&'):
                raise ValueError('syntax: expecting filename or &')
            if tt == 'word':
                redirect_target = self.peek[1]
                self.consume(tt)
            else:
                self.consume('&')
                if self.peek_token() != 'number':
                    raise ValueError('syntax: number expected after &')
                n = int(self.peek[1])
                redirect_target = ('&', n)
                self.consume('number')
            self.add_redirection(node, num, redirect_kind, redirect_target)
            tt = self.peek_token()
        parse_logger.debug('returning %r', node)
        return node

class Pipeline(WithMixin):
    """
    This class represents a pipeline of commands.

    :param source: The command line.
    :type source: str
    :param posix: Whether Posix conventions are used in the lexer.
    :type posix: bool
    :param kwargs: Whatever you might pass to :class:`subprocess.Popen'.
    """
    def __init__(self, source, posix=None, **kwargs):
        if posix is None:
            posix = os.name == 'posix'
        if isinstance(source, (list, tuple)):
            self.source = ' '.join(source)
            t = Node(kind='command', command=source, redirects={})
        else:
            self.source = source
            t = CommandLineParser().parse(source, posix=posix)
        self.tree = t
        self.last = self.find_last_command(t)
        self.events = []
        self.kwargs = kwargs
        self.stdout = kwargs.pop('stdout', None)
        self.stderr = kwargs.pop('stderr', None)
        self.lock = threading.RLock()

    def find_last_command(self, node):
        """
        Find the last command node in a parse sub-tree.

        :param node: The root of the sub-tree to search.
        :type node: An AST node from the parser.
        """
        if not hasattr(node, 'parts'):
            result = node
        else:
            result = self.find_last_command(node.parts[-1])
        assert result.kind == 'command'
        return result

    def run(self, input=None, async=False):
        """
        Run the commands in the pipeline.

        :param input: The data to pass to the command.
        :type input: Bytes, text or a file-like object of bytes.
        :param async: If True, don't wait for the pipeline to complete
                      before returning.
        :type async: bool
        """
        self.commands = []
        self.opened = []
        self.run_node(self.tree, input=input, async=async)
        return self

    @property
    def returncode(self):
        """
        The return code of the last command to run, which is regarded as the
        overall result of the pipeline.
        """
        if self.commands:
            return self.commands[-1].process.returncode

    @property
    def processes(self):
        """
        A list of the :class:`subprocess.Popen` instances for all the
        commands which have been run.
        """
        result = []
        if self.commands:
            result = [c.process for c in self.commands]
        return result

    @property
    def returncodes(self):
        """
        A list of the return codes for all the commands which have been run.
        """
        result = []
        if self.commands:
            result = [c.process.returncode for c in self.commands]
        return result

    def wait(self):
        """
        Wait for all the commands in the pipeline to complete.
        """
        logger.debug('pipeline waiting')
        for e in self.events:
            e.wait()
        for cmd in self.commands:
            logger.debug('waiting for command')
            cmd.wait()

    def close(self):
        """
        Close the pipeline.

        This waits for all the commands in the pipeline to
        complete, but also closes all the opened streams once all the commands
        have completed.
        """
        logger.debug('pipeline closing')
        for e in self.events:
            e.wait()
        for cmd in self.commands:
            cmd.wait()
            p = cmd.process
            if p is None:   # pragma: no cover
                continue
            for attr in ('stdout', 'stderr'):
                s = getattr(self, attr)
                if isinstance(s, Capture):
                    s.close()
            if p.stdout:
                p.stdout.close()
            if p.stderr:
                p.stderr.close()
        for stream in self.opened:
            stream.close()

    def run_node(self, node, input, async, event=None):
        """
        This runs a single node in the parse tree.

        :param node: The node to run.
        :type node: An AST node from the parser.
        :param input: The data to pass to the command.
        :type input: Bytes, text or a file-like object of bytes.
        :param async: If True, don't wait for the pipeline to complete
                      before returning.
        :type async: bool
        :param event: If specified, call :meth:`threading.Event.set` on the
                      event.
        :type event: :class:`threading.Event' or ``None``.
        """
        kind = node.kind
        method = 'run_%s_node' % kind
        result = getattr(self, method)(node, input, async)
        if event:
            event.set()
        return result

    def new_command(self, args, **kwargs):
        """
        Create a new :class:`Command` from the provided arguments,
        and append it to the list of commands.
        """
        cmd = Command(args, **kwargs)
        with self.lock:
            self.commands.append(cmd)
        return cmd

    def get_redirects(self, node):
        """
        Get the redirects for a node, if any.

        :param node: An AST node from the parser.
        :return: The ``stdout`` and ``stderr`` redirect targets. If either of
                 these is not specified, the corresponding value in the
                 result will be ``None``.
        :rtype: tuple
        """
        stdout = stderr = None
        for fd, fs in node.redirects.items():
            pos, fn = fs
            if pos == '>':
                mode = 'wb'
            else:
                mode = 'ab'
            if isinstance(fn, string_types):
                # Issue 9: open redirection outputs relative to cwd
                if 'cwd' in self.kwargs:
                    fn = os.path.join(self.kwargs['cwd'], fn)
                stream = open(fn, mode)
                with self.lock:
                    self.opened.append(stream)
            elif fd == 1:
                assert fn == ('&', 2)
                stream = STDERR
            elif fd == 2:
                assert fn == ('&', 1)
                stream = subprocess.STDOUT
            if fd == 1:
                stdout = stream
            else:
                stderr = stream
        return stdout, stderr

    def run_logical_node(self, node, input, async):
        """
        This runs a 'logical' node in the parse tree.

        :param node: The node to run.
        :type node: An AST node from the parser.
        :param input: The data to pass to the command.
        :type input: Bytes, text or a file-like object of bytes. Text will be
                     encoded using UTF-8.
        :param async: If True, don't wait for the pipeline to complete
                      before returning.
        :type async: bool
        """
        logger.debug('started: %s, %s, %s', node, input, async)
        parts = node.parts
        last = len(parts) - 1
        assert last > 1
        prev = None
        i = 0
        while i <= last:
            curr = parts[i]
            if prev is None:
                if not input:
                    stdin = None
                else:
                    stdin = ensure_stream(input)
            else:
                if pipe == '|':
                    stdin = prev.process.stdout
                else:
                    stdin = prev.process.stderr
            if curr.redirects == SWAP_OUTPUTS:
                stdout = STDERR
                stderr = subprocess.STDOUT
            else:
                try:
                    stdout, stderr = self.get_redirects(curr)
                except IOError:
                    if prev and stdin == prev.process.stdout:
                        stdin.close()
                    raise
            if i < last:
                pipe = parts[i + 1].pipe
                if pipe == '|':
                    assert stdout in (None, STDERR)
                    if stdout is None:
                        stdout = subprocess.PIPE
                else:
                    assert stderr in (None, subprocess.STDOUT)
                    if stderr is None:
                        stderr = subprocess.PIPE
                use_async = True
            else:
                if stdout == STDERR:
                    assert self.stdout is None
                use_async = async
            curr.cmd = self.new_command(curr.command,
                                        stdout=stdout or self.stdout,
                                        stderr=stderr or self.stderr,
                                        **self.kwargs)
            curr.cmd.run(input=stdin, async=use_async)
            # Issue 12: close stdin after spawning the child that uses it
            if prev and stdin == prev.process.stdout:
                stdin.close()
            prev = curr.cmd
            i += 2

    def run_command_node(self, node, input, async):
        """
        This runs a 'command' node in the parse tree.

        :param node: The node to run.
        :type node: An AST node from the parser.
        :param input: The data to pass to the command.
        :type input: Bytes, text or a file-like object of bytes.
        :param async: If True, don't wait for the pipeline to complete
                      before returning.
        :type async: bool
        """
        logger.debug('started: %s, %s, %s', node, input, async)
        kwargs = dict(self.kwargs)
        stdout, stderr = self.get_redirects(node)
        if node != self.last:
            kwargs['stdout'] = stdout or self.stdout
            kwargs['stderr'] = stderr or self.stderr
        else:
            if self.stdout and stdout:
                raise ValueError('You cannot redirect one stream to two '
                                 'places')
            kwargs['stdout'] = self.stdout or stdout
            if self.stderr and stderr:
                raise ValueError('You cannot redirect one stream to two '
                                 'places')
            kwargs['stderr'] = self.stderr or stderr
        node.cmd = self.new_command(node.command, **kwargs)
        node.cmd.run(input=input, async=async)

    def get_status(self, node):
        """
        Get the return code for a node. For a node with multiple commands,
        the return code of the last command (as determined by
        :meth:`find_last_command` is returned.
        """
        if node.kind == 'command':
            last = node
        else:
            last = self.find_last_command(node)
        return last.cmd.process.returncode

    def run_pipeline_node(self, node, input, async):
        """
        This runs a 'pipeline' node in the parse tree.

        :param node: The node to run.
        :type node: An AST node from the parser.
        :param input: The data to pass to the command.
        :type input: Bytes, text or a file-like object of bytes. Text will be
                     encoded using UTF-8.
        :param async: If True, don't wait for the pipeline to complete
                      before returning.
        :type async: bool
        """
        logger.debug('started: %s, %s, %s', node, input, async)
        parts = node.parts
        last = len(parts) - 1
        assert last > 1
        prev = None
        i = 0
        while i <= last:
            curr = parts[i]
            if prev is not None:
                input = None
            elif input is not None:
                input = ensure_stream(input)
                # run the current command
            if i < last:
                # need to know status, so run with async=False
                use_async = False
            else:
                use_async = async
            self.run_node(curr, input, async=use_async)
            if i < last:
                check = parts[i + 1].check
                if check == '&&':
                    if self.get_status(curr) != 0:
                        break
                else:
                    if self.get_status(curr) == 0:
                        break
            prev = curr
            i += 2

    def run_list_node(self, node, input, async):
        """
        This runs a 'list' node in the parse tree.

        :param node: The node to run.
        :type node: An AST node from the parser.
        :param input: The data to pass to the command.
        :type input: Bytes, text or a file-like object of bytes. Text will be
                     encoded using UTF-8.
        :param async: If True, don't wait for the pipeline to complete
                      before returning.
        :type async: bool
        """
        logger.debug('started: %s, %s, %s', node, input, async)
        parts = node.parts
        last = len(parts) - 1
        assert last > 1
        prev = None
        i = 0
        while i <= last:
            curr = parts[i]
            if prev is not None:
                input = None
            elif input is not None:
                input = ensure_stream(input)
            if i < last:
                use_async = parts[i + 1].sync == '&'
            else:
                use_async = async
                # run the current command
            if not use_async:
                self.run_node(curr, input, async=use_async)
            else:
                # When the node is run in a separate thread, we need
                # a sync point for when all the commands have been created
                # for that node - even when there are delays because of e.g.
                # sleep commands or other time-consuming commands. That's
                # what these events are for - they're set at the end of
                # run_node, and waited on int the pipeline's wait and run
                # methods.
                e = threading.Event()
                with self.lock:
                    self.events.append(e)
                t = threading.Thread(target=self.run_node, args=(curr, input,
                                     False, e))
                t.daemon = True
                t.start()
            prev = curr
            i += 2


# Module-level convenience functions

def run(cmd, **kwargs):
    """
    Run a command with optional input and either synchronously or
    asynchronously.

    :param cmd:    The command string or array or command/args to be run.
    :type cmd:     This is the same as the first argument to the constructor of
                   :class:`subprocess.Popen'.
    :param input: The input to pass to the command subprocess.
    :type input:  If this is text, it is encoded to bytes using UTF-8. If
                  it is a byte string, it is used as is. Otherwise, a
                  file-like object containing bytes should be passed: this
                  will be read to the end, but not closed.
    :param async: If ``True``, this method returns without waiting for the
                  subprocess to complete. Otherwise, it awaits completion
                  by calling the :meth:`subprocess.Popen.wait` method.
    :type async:  bool
    """
    input = kwargs.pop('input', None)
    async = kwargs.pop('async', False)
    if async:
        p = Pipeline(cmd, **kwargs)
        p.run(input=input, async=True)
    else:
        with Pipeline(cmd, **kwargs) as p:
            p.run(input=input, async=async)
    return p

def capture_stdout(cmd, **kwargs):
    """
    This is the same as :func:`run`, but the ``stdout`` is captured. You can
    access this via the ``stdout`` attribute of the return value from this
    function.
    """
    kwargs['stdout'] = Capture()
    return run(cmd, **kwargs)

def capture_stderr(cmd, **kwargs):
    """
    This is the same as :func:`run`, but the ``stderr`` is captured. You can
    access this via the ``stderr`` attribute of the return value from this
    function.
    """
    kwargs['stderr'] = Capture()
    return run(cmd, **kwargs)

def capture_both(cmd, **kwargs):
    """
    This is the same as :func:`run`, but the ``stdout`` and ``stderr`` are
    both captured. You can access these via the ``stdout`` and
    ``stderr`` attributes of the return value from this function.
    """
    kwargs['stdout'] = Capture()
    kwargs['stderr'] = Capture()
    return run(cmd, **kwargs)

def get_stdout(cmd, **kwargs):
    """
    This is the same as :func:`capture_stdout`, but it returns the captured
    text. Use this when you know the output will not be voluminous - it will
    be buffered in memory.
    """
    p = capture_stdout(cmd, **kwargs)
    return p.stdout.text

def get_stderr(cmd, **kwargs):
    """
    This is the same as :func:`capture_stderr`, but it returns the captured
    text. Use this when you know the output will not be voluminous - it will
    be buffered in memory.
    """
    p = capture_stderr(cmd, **kwargs)
    return p.stderr.text

def get_both(cmd, **kwargs):
    """
    This is the same as :func:`capture_both`, but it returns the captured
    text from the two streams as a 2-element tuple, with the ``stdout`` text as
    the first element and the ``stderr`` text as the second. Use this when you
    know the output will not be voluminous - it will be buffered in memory.
    """
    p = capture_both(cmd, **kwargs)
    return p.stdout.text, p.stderr.text


def parse_command_line(source, posix=None):
    """
    Parse a command line into an AST.

    :param source: The command line to parse.
    :type source: str
    :param posix: Whether Posix conventions are used in the lexer.
    :type posix: bool
    """
    if posix is None:
        posix = os.name == 'posix'
    return CommandLineParser().parse(source, posix=posix)
