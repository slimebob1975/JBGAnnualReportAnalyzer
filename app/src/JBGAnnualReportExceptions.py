class JBGAnnualReportError(Exception):
    """Base class for all errors raised by the annual report analyzer."""

    default_message = "Okänt fel"

    def __init__(self, message: str = None):
        self.message = message if message is not None else self.default_message
        super().__init__(self.message)


class FileTypeException(JBGAnnualReportError):
    default_message = "Ogiltig filtyp"


class EmptyOutputException(JBGAnnualReportError):
    default_message = "Tomt utdata"
