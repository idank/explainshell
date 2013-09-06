class ProgramDoesNotExist(Exception):
    pass

class EmptyManpage(Exception):
    pass

class ParsingError(Exception):
    def __init__(self, message, s, position):
        self.origmessage = message
        self.s = s
        self.position = position

        assert position < len(s)

        prefix = '%s (position %d, ' % (message, position)
        indent = len(prefix)
        if position - 5 > 0:
            indent -= (position - 5)
        indent += position
        message = prefix + '%s)\n%s^' % (s[max(0, position - 5):], (indent * ' '))
        super(ParsingError, self).__init__(message)
