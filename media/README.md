# Media

Radio streaming, music link nominations and social embed fixing in one cog. It replaces the separate
Radio, MusicBoard and EmbedFixer cogs and keeps their saved settings.

## Features

**Radio** streams an HTTP/HTTPS URL into a voice channel with FFmpeg. With auto play on, the bot joins the
auto channel when someone is in it and leaves about 10 seconds after it empties. If the stream drops it
reconnects. While connected it keeps the voice channel status set to the current show and track (read from
the Icecast `status-json.xsl`), and it can keep a now-playing message updated in a text channel.

**MusicBoard** adds 🔗 and ⛓️‍💥 to messages with a music link (YouTube, YouTube Music, Spotify, SoundCloud,
Apple Music, Tidal, Deezer, Bandcamp, Amazon Music). 🔗 posts the link to the music channel and marks the
message ✅. ⛓️‍💥 blocks it with ❌. Each message is posted once. Unanswered prompts disappear after the reaction
timeout (10 minutes by default).

**EmbedFixer** reposts supported links (Twitter/X, Pixiv, TikTok, Reddit, Instagram, FurAffinity, Twitch clips,
Iwara, Bluesky, Facebook, Bilibili and Bilibili Opus, Tumblr, Threads, PTT, DeviantArt, Pinterest) through a
webhook with the author's name and avatar, using a service that embeds properly, and hides the original embeds.
YouTube links (through Koutube) are off by default; turn on **Fix YouTube links** in the panel.
Set **Translate Twitter/X posts** in the panel (a code like `pt` or a name like `Portuguese`) and Twitter/X
links fixed through FxEmbed show the post translated into that language. Clear it to turn translation off.
Translation only works with FxEmbed, so it stops if someone presses **Try other** to switch to another service. A **Try other**
button switches to the next service for a day after posting. Skip one link with `$link` or `<link>`.
The site and service table follows upstream [embed-fixer](https://github.com/seriaati/embed-fixer) 1.9.8.

## Commands

| Command | Who | What |
| --- | --- | --- |
| `/media status` (`[p]media`) | Everyone | Status of all three features and your EmbedFixer choice |
| `/media play [channel]` | Everyone | Join voice and start the radio |
| `/media stop` | Everyone | Stop the radio and leave voice |
| `/media fixlinks <enabled>` | Everyone | Choose whether EmbedFixer reposts your links in this server |
| `/media clearboard` | Manage Server | Let already-posted MusicBoard links be nominated again |

## Settings

All settings are in `/control` under **Media** (defaults in brackets):

- **Radio**: on/off, stream URL, auto channel, auto play, reconnect, voice channel status, status channel,
  status API URL, volume (100%, applied live), leave delay after the channel empties (10 s), reconnect delay
  (5 s), show schedule (on; off uses the station name, `LongWayFM`), status message title, voice status
  formats for playing (`{show}: {title}`) and offline (`{show}: offline`), and, for bot owners only, the
  status refresh interval for every server (30 s).
- **MusicBoard**: on/off, music channel, reaction timeout, watched channels (all), votes needed (1), block
  reaction on/off, the nominate/block/posted/blocked emojis, how long the blocked mark stays (60 s) and the
  nomination message (`{user}`, `{jump}`, `{url}`).
- **MusicBoard links**: one switch per accepted link type (YouTube, YouTube Music, Spotify, SoundCloud, Apple
  Music, Tidal, Deezer, Bandcamp, Amazon Music).
- **EmbedFixer**: automatic fixing, YouTube links, Twitter/X translation, ignored channels and roles, repost as
  the author through a webhook or as the bot (a reply), what happens to the original (hide its embeds, delete
  it, or leave it; deleting reposts the whole message and falls back to hiding embeds when it has files),
  the **Try other** button on/off and its lifetime (24 h).
- **EmbedFixer sites**: one switch per supported site.
- **EmbedFixer services**: the service tried first for each site with more than one; **Try other** moves on
  from it.

Turning a feature off stops its listener in that server. Disabling the whole cog with Red's
`[p]command disablecog Media` also stops all three.

## Moving from the old cogs

```
[p]unload radio musicboard embedfixer
[p]repo update <repo>
[p]cog install <repo> media
[p]load media
[p]slash enablecog media
[p]slash sync
[p]cog uninstall radio musicboard embedfixer
```

Unload the old cogs before loading Media, or both will react to the same messages.

Settings are stored under the old names (`Radio`, `MusicBoard`, `EmbedFixer`), so nothing needs to be moved.

## Requirements

FFmpeg on the host and PyNaCl in the bot environment for Radio. The bot needs Manage Webhooks and Manage
Messages for EmbedFixer to repost and hide embeds, and Add Reactions and Manage Messages for MusicBoard.
