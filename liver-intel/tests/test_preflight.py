from liver_intel.feeds import Feed, Registry
from liver_intel.http import Blocked
from liver_intel.preflight import check, hosts_from, summarise


def registry():
    return Registry(entries=[
        Feed(id="a", url="https://one.example.com/feed", task="T1", source="newswire"),
        Feed(id="b", url="https://one.example.com/other", task="T2", source="edgar"),
        Feed(id="c", url="https://two.example.com/api", task="T3", source="ctgov"),
    ])


def test_hosts_are_deduplicated_and_tasks_merged():
    hosts = hosts_from(registry())
    assert hosts["one.example.com"][1] == {"T1", "T2"}


def test_extra_api_hosts_are_included():
    assert "www.accessdata.fda.gov" in hosts_from(registry())


def test_reachable_host_reports_ok(fake_fetcher):
    fake_fetcher.add("https://", "hi")          # prefix match: everything resolves
    results = {r.host: r for r in check(registry(), fake_fetcher)}
    assert results["one.example.com"].status == "ok"


def test_http_error_still_counts_as_reachable(fake_fetcher):
    """A 404 proves the connection got through; only a refused tunnel does not."""
    fake_fetcher.add("https://", "nope", status=404)
    result = next(r for r in check(registry(), fake_fetcher)
                  if r.host == "two.example.com")
    assert result.status == "http-error"
    assert result.reachable is True


def test_blocked_host_is_reported_as_blocked(fake_fetcher):
    fake_fetcher.routes["https://"] = Blocked("403")
    results = check(registry(), fake_fetcher)
    assert all(r.status == "blocked" for r in results)
    assert all(not r.reachable for r in results)


def test_summary_blames_the_egress_policy_when_nothing_is_reachable(fake_fetcher):
    fake_fetcher.routes["https://"] = Blocked("403")
    message = summarise(check(registry(), fake_fetcher))
    assert "egress policy" in message
    assert "new session" in message


def test_summary_names_the_hosts_still_missing(fake_fetcher):
    fake_fetcher.add("https://one.example.com", "ok")
    fake_fetcher.routes["https://two.example.com"] = Blocked("403")
    fake_fetcher.routes["https://www."] = Blocked("403")
    message = summarise(check(registry(), fake_fetcher))
    assert "two.example.com" in message
