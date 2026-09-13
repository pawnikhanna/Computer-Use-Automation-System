import recoverable

class FakeLocator:
    def __init__(self, log, label):
        self.log = log
        self.label = label

    def click(self, timeout=2000):
        self.log.append(("clicked", self.label))


class FakePage:
    """Stands in for a Playwright Page without needing a real browser --
    enough to test the recoverable-condition dismissal logic in isolation."""
    def __init__(self):
        self.calls = []

    def get_by_role(self, role, name):
        self.calls.append(("get_by_role", role, name))
        return FakeLocator(self.calls, name)

    def get_by_text(self, text, exact=True):
        self.calls.append(("get_by_text", text))
        return FakeLocator(self.calls, text)


def test_dismisses_known_condition_when_text_present():
    page = FakePage()
    name = recoverable.try_dismiss(page, recoverable.KNOWN_CONDITIONS, "SYSTEM NOTICE: balances may be delayed")
    assert name == "system_notice_interstitial"
    assert ("clicked", "Acknowledge") in page.calls


def test_no_dismissal_when_condition_absent():
    page = FakePage()
    name = recoverable.try_dismiss(page, recoverable.KNOWN_CONDITIONS, "Welcome, operator.")
    assert name is None
    assert page.calls == []