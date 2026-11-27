# Lillio portal: what this tool relies on

Lillio (formerly HiMama) publishes no API for parents. This tool uses the pages and JSON that the parent web
portal itself loads. Everything here can change without notice.

## Authentication

Rails session auth.

1. `GET /login`: read the hidden `authenticity_token` input (CSRF token). The response sets the session cookie.
2. `POST /login` (form encoded): `authenticity_token`, `user[login]`, `user[password]`, `user[remember_me]=1`,
   `commit=Log In`. Success redirects away from `/login`; failure re-renders the form.

The session cookie has historically been named `_himama_session` and is long-lived, which is why a cookie can be
supplied instead of a password (`LILLIO_SESSION_COOKIE`). Accounts that sign in through SSO or a social provider
have no password and must use the cookie.

A request made with an expired session redirects to `/login`; the client reports that as an authentication error.

## Children

`GET /accounts` and `GET /headlines` contain links of the form `/accounts/<child id>` whose text is the child's
name. Both pages are scanned and merged. If discovery finds nothing, set `LILLIO_CHILD_IDS` from the number in the
portal URL when viewing a child.

## Journal (photos and videos)

`GET /accounts/<child id>/journal_api?page=<n>` (n from 1) returns JSON, newest first:

```json
{
  "intervals": [
    [
      {"activity": {
        "id": 123, "activity_file_id": 456,
        "title": "...", "description": "...",
        "created_at": "2026-03-02T10:15:00.000-05:00",
        "image": {"url": "https://...", "big": {"url": "https://..."}},
        "video": {"url": "https://..."}
      }}
    ]
  ]
}
```

- `intervals` may be a list or an object keyed by date; both are accepted.
- An activity can carry an image, a video, both, or neither (text-only entries are skipped).
- For images the largest offered size is used (`original`, then `big`, `large`, `full`, `medium`, then `url`).
- Media URLs point at a storage host and are fetched without portal cookies.
- The dedupe key is `<child id>:<activity_file_id or id>:<image|video>`, falling back to a hash of the URL path.
- `"Look what I'm doing today!"` is the portal's default caption and is dropped.
- Paging ends at the first page with no activities.

## Verification status

The request and response shapes above come from two independent open-source tools for this portal
([tkuppens/kindercare](https://github.com/tkuppens/kindercare), JSON journal;
[meagar/himama-dl](https://github.com/meagar/himama-dl), login and child discovery) and from a prior private
integration that used the same login flow. Run `lillio-immich-sync probe` against your account to print the
current response structure, with captions, names and signed URLs removed, and compare it with the above.
