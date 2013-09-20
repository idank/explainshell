class ProgramDoesNotExist(Exception):
    pass

class EmptyManpage(Exception):
    pass

class ParsingError(Exception):
    def __init__(self, message, s, position):
        self.message = message
        self.s = s
        self.position = position

        assert position <= len(s)
        super(ParsingError, self).__init__('%s (position %d)' % (message, position))
