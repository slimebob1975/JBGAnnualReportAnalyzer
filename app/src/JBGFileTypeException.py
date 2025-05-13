class FileTypeException(BaseException):
    def __init__(self, message="Ogiltig filtyp"):
        self.message = message
        super().__init__(self.message)
        
class EmptyOutputException(BaseException):
    def __init__(self, message="Tomt utdata"):
        self.message = message
        super().__init__(self.message)