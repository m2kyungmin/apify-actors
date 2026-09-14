# Threads Scraper: Profiles, Posts, Replies & Search

> **Run it on Apify Store:** https://apify.com/kyungminlee/threads-scraper — pay per result, no setup. This folder is the Actor's source code (Python, `httpx`, Apify SDK).

Extract public data from **Threads** (threads.com) without an account, cookies,
or a browser. Give the Actor `@handles`, post URLs, or search keywords and get
clean JSON: post text, images and videos, like / reply / repost / quote counts,
timestamps, link previews, and author details — plus profile records with bio,
follower count, and verification status. This is an **unofficial** tool and is
not affiliated with Meta.

## What does Threads Scraper do?

- **Profiles** – one profile record (name, bio, bio links, followers, verified,
  avatar) and the newest posts of the profile, paginated as deep as you ask.
- **Posts** – paste a post URL to get the post and the top replies shown on its
  page (about 20 per post in the logged-out view), each as its own record.
- **Search** – keyword search returns the first results page Threads shows to
  visitors (about 20 posts per query).

Everything is read from the same public pages and endpoints a logged-out
browser uses, so no login is ever required and no account can be banned.

## Why use Threads Scraper?

- **No cookies, no risk** – only logged-out data is read; you never hand over
  session tokens.
- **Engagement metrics included** – likes, replies, reposts, quotes, and
  reshares per post.
- **Media URLs** – full-resolution image and MP4 video URLs, including every
  item of carousel posts.
- **Fast and cheap** – no browser is started; a profile page plus one
  pagination request delivers ~15 posts in about a second.
- **Pay per result** – you are charged per post and per profile actually
  saved. Failed handles, missing posts, and empty searches cost nothing.

## How to use it

1. Enter one or more **Profiles** (`@zuck` or `https://www.threads.com/@zuck`)
   and set **Maximum posts per profile**.
2. Optionally add **Post URLs** and decide whether to **Include replies**.
3. Optionally add **Search queries**.
4. Click **Start**. Results appear in the **Dataset** tab; export as JSON, CSV,
   or Excel, or fetch them through the API and integrations.

## Input

| Field | Type | Default | Notes |
|---|---|---|---|
| `profiles` | array of strings | – | `@handle` or profile URLs. |
| `maxPostsPerProfile` | integer | 50 | Newest posts per profile (1–2000). |
| `postUrls` | array of strings | – | `https://www.threads.com/@user/post/CODE`. |
| `includeReplies` | boolean | `true` | Save the replies shown on each post page. |
| `searchQueries` | array of strings | – | Keywords; first results page per query. |
| `proxyConfiguration` | object | Apify Proxy on | Session rotates every 15 requests. |
| `profilePostsDocId` | string | (current) | Advanced: Threads' GraphQL query id for profile pagination; change only if pagination breaks after a Threads update. |

Example:

```json
{
  "profiles": ["@zuck", "@meta"],
  "maxPostsPerProfile": 100,
  "postUrls": ["https://www.threads.com/@zuck/post/Db2wI-DilLt"],
  "searchQueries": ["open source AI"]
}
```

## Output

Each item has `type` (`post` or `profile`), `source` (which handle, URL, or
query produced it) and `scrapedAt`.

```json
{
  "type": "post",
  "id": "3960564644938666733",
  "code": "Db2wI-DilLt",
  "url": "https://www.threads.com/@zuck/post/Db2wI-DilLt",
  "text": "Today we're also opening the weights for Muse Glimmer ...",
  "createdAt": "2026-08-10T10:01:59+00:00",
  "likeCount": 1945,
  "replyCount": 510,
  "repostCount": 88,
  "quoteCount": 46,
  "reshareCount": 91,
  "mediaType": "text",
  "images": [],
  "videos": [],
  "linkPreview": null,
  "isReply": false,
  "author": {"id": "63055343223", "username": "zuck", "fullName": "Mark Zuckerberg", "isVerified": true},
  "authorUsername": "zuck",
  "source": "profile:@zuck",
  "scrapedAt": "2026-09-15T02:30:00+00:00"
}
```

## Data fields

**Post** (`type: "post"`): `id`, `code`, `url`, `text`, `createdAt`,
`likeCount`, `replyCount`, `repostCount`, `quoteCount`, `reshareCount`,
`mediaType` (`text`, `image`, `video`, `carousel`), `images[]` (`url`,
`width`, `height`), `videos[]` (`url`, `width`, `height`, `thumbnailUrl`),
`linkPreview` (`url`, `displayUrl`, `title`, `imageUrl`), `isReply`,
`replyToUsername`, `replyToPostCode` (for replies collected from a post URL),
`language`, `isPaidPartnership`, `author` (`id`, `username`, `fullName`,
`isVerified`, `profilePicUrl`, `profileUrl`), `authorUsername`.

**Profile** (`type: "profile"`): `id`, `username`, `url`, `fullName`, `bio`,
`bioLinks[]`, `followerCount`, `isVerified`, `isPrivate`, `profilePicUrl`.

## Pricing

Pay per event: one `post` event for every post or reply saved and one
`profile` event for every profile record. Failed or private profiles, missing
posts, and empty searches are never charged. Set a maximum charge per run to
cap spending; the Actor stops cleanly when the limit is reached.

## Tips

- Threads shows logged-out visitors the top ~20 replies of a post and the first
  ~20 search results; deeper reply and search pagination requires a login and
  is out of scope.
- Profile pagination goes as deep as Threads allows for public profiles;
  `maxPostsPerProfile` caps it.
- Private profiles and deleted posts return an `error`-level warning in the
  log and are skipped without charge.
- Keep Apify Proxy enabled for large runs; Threads tolerates moderate request
  rates but rotating sessions avoids throttling.

## FAQ

**Does it need my Threads / Instagram login?** No, and it never asks for it.
Only public, logged-out data is collected.

**Why did pagination stop early on a profile?** Meta ships new web builds
regularly and occasionally changes the GraphQL query id. The Actor then
delivers the posts it already fetched and logs a warning; the `profilePostsDocId`
input lets you supply the new id immediately, and the Actor is updated
promptly.

**Can it scrape the home feed, followers, or likes lists?** No. Those views
require an account.

**Is scraping Threads legal?** The Actor reads only publicly visible content.
You are responsible for using the data in line with Meta's terms and the
privacy laws that apply to you, especially when storing author information.

**Something is broken.** Open an issue on the Actor page; issues are answered
within a few days.
