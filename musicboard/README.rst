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

Let users nominate YouTube links to a dedicated music channel by reacting with 🔗.
When a user reacts to any message containing a YouTube link with the 🔗 emoji, the bot automatically posts a rich embed to the configured music channel with the video title, thumbnail, who nominated it, and a jump link to the original message.
Each link can only be nominated once — duplicate nominations are silently ignored. A ✅ reaction is added to the original message to confirm the nomination was posted.

---------
Commands:
---------

Here are all the commands included in this cog (4):

* ``[p]musicboard``
 Manage MusicBoard settings. Running this command without a subcommand shows the current configuration.

* ``[p]musicboard channel <channel>``
 Set the channel where nominated music links get posted. Requires **Manage Guild** permission.

* ``[p]musicboard show``
 Show the current MusicBoard configuration, including the music channel and the total number of tracks posted. Requires **Manage Guild** permission.

* ``[p]musicboard clear``
 Clear the deduplication list so previously nominated links can be nominated again. Requires **Manage Guild** permission.

-----
Setup
-----

1. Load the cog: ``[p]cog load musicboard``
2. Set a music channel: ``[p]musicboard channel #music``
3. Done — members can now react with 🔗 on any message containing a YouTube link to nominate it.
