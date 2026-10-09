import time

import httpx

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

RETRY_STATUSES = {429, 500, 502, 503, 504}


class EdgarClient:
    """Thin EDGAR client that respects the SEC fair-access policy.

    The SEC caps clients at 10 requests/second and requires a User-Agent with
    contact details. We stay under the cap with a minimum interval between calls.
    """

    def __init__(self, user_agent: str, max_per_second: float = 8.0, max_retries: int = 4):
        self._http = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=30.0,
        )
        self._min_interval = 1.0 / max_per_second
        self._max_retries = max_retries
        self._last_request = 0.0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._http.close()

    def get_json(self, url: str) -> dict:
        for attempt in range(self._max_retries + 1):
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

            response = self._http.get(url)
            if response.status_code in RETRY_STATUSES and attempt < self._max_retries:
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            return response.json()
        raise AssertionError("unreachable")

    def ticker_map(self) -> dict[str, int]:
        """Ticker -> CIK for currently listed companies (delisted ones are absent)."""
        data = self.get_json(TICKERS_URL)
        return {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}

    def company_facts(self, cik: int) -> dict:
        return self.get_json(FACTS_URL.format(cik=cik))

    def submissions(self, cik: int) -> dict:
        return self.get_json(SUBMISSIONS_URL.format(cik=cik))
