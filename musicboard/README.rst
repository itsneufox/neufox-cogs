.. _musicboard:
===========
MusicBoard
===========

This is the cog guide for the ``MusicBoard`` cog. This guide contains the collection of commands which you can use in the cog.
Through this guide, ``[p]`` will always represent your prefix. Replace ``[p]`` with your own prefix when you use these commands in Discord.

.. note::

    Ensure that you are up to date by running ``[p]cog update musicboard``.
    If there is something missing, or something that needs improving in this documentation, feel free to create an issue `here <https://github.com/itsneufox/neufox-cogs/issues>`_.
    This documentation is generated everytime this cog receives an update.

---------------
About this cog:
---------------

Let users nominate music links to a dedicated channel by reacting with 🔗.
When a message containing a supported music link is posted, the bot automatically adds a 🔗 and ⛓️‍💥 reaction to it. Reacting with 🔗 nominates the link to the configured music channel. Reacting with ⛓️‍💥 blocks it from being nominated.
Each link can only be nominated once, the 🔗 reaction is replaced with ✅ after a successful nomination.
Unresolved 🔗 and ⛓️‍💥 prompt reactions expire automatically after 10 minutes by default. Blocked-link ❌ reactions expire after 1 minute, while successful ✅ reactions stay.

**Supported platforms:**

- YouTube & YouTube Music
- Spotify
- SoundCloud
- Apple Music
- Tidal
- Deezer
- Bandcamp
- Amazon Music

---------
Commands:
---------

Here are all the commands included in this cog (5):

* ``[p]musicboard``
 Manage MusicBoard settings. Running this command without a subcommand shows the current configuration.

* ``[p]musicboard channel <channel>``
 Set the channel where nominated music links get posted. Requires **Manage Guild** permission.

* ``[p]musicboard show``
 Show the current MusicBoard configuration, including the music channel and the total number of tracks posted. Requires **Manage Guild** permission.

* ``[p]musicboard timeout [minutes]``
 Show or set how long pending MusicBoard prompt reactions stay on a message. Use ``0`` to disable automatic expiry. Requires **Manage Guild** permission.

* ``[p]musicboard clear``
 Clear the deduplication list so previously nominated links can be nominated again. Requires **Manage Guild** permission.

-----
Setup
-----

1. Load the cog: ``[p]cog load musicboard``
2. Set a music channel: ``[p]musicboard channel #music``
3. Done, the bot will automatically react to any music link posted in the server.
