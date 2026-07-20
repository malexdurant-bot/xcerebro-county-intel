"""
Debug: Maricopa Superior Court Probate POST response.
Run: python runs/maricopa_az/debug_probate_post.py
"""
import http.cookiejar as cookiejar
import ssl
import urllib.parse
import urllib.request

PORTAL_URL = "https://www.superiorcourt.maricopa.gov/docket/ProbateCourtCases/caseSearch.asp"
USER_AGENT = "xcerebro-test/0.1"

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

jar = cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar),
    urllib.request.HTTPSHandler(context=ssl_ctx),
)

# Step 1: GET the form page
headers_get = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
req_get = urllib.request.Request(PORTAL_URL, headers=headers_get)
with opener.open(req_get, timeout=30) as resp:
    html_get = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")

print(f"GET response length: {len(html_get)}")
print(f"Cookies after GET: {list(str(c) for c in jar)}")

# Step 2: POST with search params
post_data = urllib.parse.urlencode({
    "lastName": "Smith",
    "FirstName": "",
    "caseNumber": "",
}).encode("utf-8")

req_post = urllib.request.Request(
    PORTAL_URL,
    data=post_data,
    headers={
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": PORTAL_URL,
    },
)
with opener.open(req_post, timeout=30) as resp:
    html_post = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
    final_url = resp.url

print(f"POST response length: {len(html_post)}")
print(f"Final URL after POST: {final_url}")
print()
print("--- First 2000 chars of POST response ---")
print(html_post[:2000])
print()

# Check for table presence
if "<table" in html_post.lower():
    print(">>> TABLE found in response")
else:
    print(">>> NO TABLE in response")

# Check for "no results" or error messages
for phrase in ["no results", "no records", "no cases", "error", "invalid", "try again", "required"]:
    if phrase in html_post.lower():
        print(f">>> Found phrase: '{phrase}'")
