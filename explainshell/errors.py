class ProgramDoesNotExist(Exception):
    pass

class EmptyManpage(Exception):
    pass

class ParsingError(Exception):
    def __init__(self, message, s, position):
        self.origmessage = message
        self.s = s
        self.position = position

        message = '%s (position %d, ...%s)' % (message, position, s[max(0, position-5):])
        super(ParsingError, self).__init__(message)
