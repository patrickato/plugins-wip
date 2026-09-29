"""Minimal stand-in for the real `apprise` pip package, used only because
the sandbox this test suite runs in has no network access to install the
real package. Shape matches the real library's public API closely enough
for AppriseNotifyNG's usage: Apprise(), .add(url_or_config), .notify(...),
and AppriseConfig(). Tests that need to assert on *what* got notified patch
apprise.Apprise with a mock/fake themselves; this stub exists so `import
apprise` succeeds and a bare Apprise()/AppriseConfig() behaves sanely.
"""


class AppriseConfig:
    def __init__(self):
        self.sources = []

    def add(self, source):
        self.sources.append(source)
        return True


class Apprise:
    def __init__(self):
        self.added = []
        self.notifications = []

    def add(self, url_or_config):
        self.added.append(url_or_config)
        return True

    def notify(self, title=None, body=None, attach=None):
        self.notifications.append({"title": title, "body": body, "attach": attach})
        return True
