class Intents:
    @staticmethod
    def default():
        return Intents()

    def __init__(self):
        self.message_content = False


class File:
    def __init__(self, fp=None, filename=None):
        self.fp = fp
        self.filename = filename
