# project-blacktape — Vision

> This document is aspirational. For what the repo actually does today,
> see [README.md](README.md).

**Offline. Open Source. Built to help you understand the data you already own.**

## What is project-blacktape?

**project-blacktape** is an offline-first digital archive exploration
platform designed to make exported account data understandable.

Companies like Google, Snapchat, Instagram, Facebook, Discord, and many
others allow you to download your personal data. While those exports
contain a wealth of information, they're often delivered as thousands of
files, nested directories, JSON documents, metadata, and media assets
that are difficult to explore without specialized tools.

project-blacktape transforms those raw archives into a searchable,
human-readable workspace — without ever sending your data to the cloud.

Whether you're preserving memories, researching your digital footprint,
auditing your own information, or performing digital investigations,
project-blacktape helps you explore the data you've already been given.

## Why?

Downloading your data shouldn't be the end of the journey.

Most data exports look something like this:

```
Takeout/
├── Activity/
├── Photos/
├── Maps/
├── Messages/
├── Profile/
├── Metadata/
└── thousands of JSON files...
```

Technically complete. Practically unreadable.

project-blacktape exists to bridge that gap.

## Philosophy

Your data belongs to **you**. Not us. Not a cloud service. Not an AI.
Not a subscription.

Everything project-blacktape does happens on **your computer**. No
telemetry. No accounts. No tracking. No internet connection required.
No uploading your personal history to someone else's servers.

Privacy isn't a feature. It's the default.

## Vision

The long-term goal is to build a universal archive explorer capable of
understanding exports from many online services through a shared parser
ecosystem.

Rather than writing one viewer for every platform, project-blacktape
aims to provide a consistent experience regardless of where the archive
originated.

Supported services may eventually include:

- Google Takeout
- Snapchat
- Instagram
- Facebook
- Discord
- Reddit
- X (Twitter)
- Email archives
- Browser history
- GPS exports
- Photo libraries
- Custom datasets

Adding support for new formats should require writing a new parser — not
redesigning the application.

## Planned features

### Archive import
ZIP, TAR, TAR.GZ, folder imports.

### Intelligent archive detection
Automatically identify supported export formats and route them to the
correct parser.

### Timeline explorer
Browse years of activity chronologically — messages, photos, videos,
searches, location history, account events.

### Search everything
Fast indexing across messages, contacts, usernames, metadata, locations,
files, dates.

### Media browser
Browse photos and videos alongside the metadata that created them.

### Conversation viewer
Reconstruct messaging history into readable conversations instead of
raw JSON.

### Maps
Visualize GPS history and location data.

### Metadata explorer
Inspect devices, login history, friends, contacts, account changes,
export metadata.

### Plugin system
Support for extending project-blacktape without modifying the core
application — new parsers, visualizations, reports, transformations,
exporters.

## Design principles

Every feature should satisfy at least one of these goals:

- Keep archives read-only.
- Never modify original evidence.
- Work completely offline.
- Be understandable by non-programmers.
- Scale to very large datasets.
- Remain extensible.
- Be fast enough that users forget how large their archive actually is.

If a feature doesn't improve one of those principles, it probably
doesn't belong.

## Roadmap

- [x] Rust parser workspace
- [x] Shared parser utilities
- [ ] Unified parser framework
- [ ] Archive detection
- [ ] Search index
- [ ] Python desktop application
- [ ] Timeline explorer
- [ ] Media browser
- [ ] Interactive maps
- [ ] Plugin SDK
- [ ] Stable v1.0

## Contributing

project-blacktape is still in its early stages, and contributions of all
kinds are welcome — Rust, Python, digital forensics, reverse-engineering
export formats, documentation, UI/UX, performance, testing. Even opening
issues, suggesting ideas, or sharing unusual archive formats can make a
meaningful difference.

## One last thing

The internet remembers everything. You should be able to understand
what it remembers about **you**.

**project-blacktape** is here to help.
