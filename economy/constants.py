"""Shared constants and defaults for the Economy cog."""

from __future__ import annotations

import discord

CASH = "cash"
CURRENCY_NAME = "LWD$"
CURRENCY_FIELD_NAME = "LWD$"
CONFIG_IDENTIFIER = 830271946

DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8787

# Claims
DEFAULT_DAILY_AMOUNT = 250
DEFAULT_DAILY_COOLDOWN = 86400
DEFAULT_WEEKLY_AMOUNT = 1000
DEFAULT_WEEKLY_COOLDOWN = 604800
DEFAULT_MONTHLY_AMOUNT = 2500
DEFAULT_MONTHLY_COOLDOWN = 2592000
DEFAULT_ANNUAL_AMOUNT = 12000
DEFAULT_ANNUAL_COOLDOWN = 31536000
DEFAULT_WORK_AMOUNT = 75
DEFAULT_WORK_MIN = 25
DEFAULT_WORK_MAX = 100
DEFAULT_WORK_COOLDOWN = 3600
DEFAULT_STREAK_BONUS_PER_DAY = 25
DEFAULT_STREAK_BONUS_CAP = 250

# Casino
DEFAULT_CASINO_MIN_BET = 10
DEFAULT_CASINO_MAX_BET = 50000
DEFAULT_CASINO_RAKE_PERCENT = 0.0
CASINO_COINFLIP_PAYOUT_PERCENT = 195
CASINO_DICE_PAYOUT_PERCENT = 570

# Lottery
DEFAULT_LWDMILLIONS_TICKET_PRICE = 100
DEFAULT_LWDMILLIONS_SEED_JACKPOT = 1_000_000
DEFAULT_LWDMILLIONS_JACKPOT_CONTRIBUTION_PERCENT = 50
MAX_LWDMILLIONS_LINES_PER_PLAYER = 50
MAX_LWDMILLIONS_TOTAL_TICKETS = 10_000
LWDMILLIONS_HISTORY_LIMIT = 10
LWDMILLIONS_HISTORY_WINNER_LIMIT = 100
LWDMILLIONS_DRAW_POLL_SECONDS = 30
DRAND_FETCH_TIMEOUT_SECONDS = 12
DRAND_REQUIRED_CONSENSUS = 2
DRAND_QUICKNET_ENDPOINTS = (
    "https://api.drand.sh",
    "https://api2.drand.sh",
    "https://api3.drand.sh",
    "https://drand.cloudflare.com",
)

# Boosters, streak savers and the wealth tax (global, bot owner)
DEFAULT_STREAK_SAVER_PRICE = 1500
MAX_STREAK_SAVERS = 5
DEFAULT_CLAIM_BOOSTER_PRICE = 4000
DEFAULT_CLAIM_BOOSTER_HOURS = 24
DEFAULT_CLAIM_BOOSTER_MULTIPLIER = 1.5
DEFAULT_XP_BOOSTER_PRICE = 3000
DEFAULT_XP_BOOSTER_HOURS = 24
DEFAULT_XP_BOOSTER_MULTIPLIER = 2.0
MAX_BOOSTER_HOURS = 24 * 30
DEFAULT_WEALTH_TAX_PERCENT = 0.0
DEFAULT_WEALTH_TAX_THRESHOLD = 1_000_000
DEFAULT_WEALTH_TAX_CAP = 25_000

# Server sinks (server admins)
DEFAULT_PAY_TAX_PERCENT = 2.0
DEFAULT_HOUSE_CUT_PERCENT = 5.0
DEFAULT_TITLE_PRICE = 5000
DEFAULT_BADGE_PRICE = 2500
DEFAULT_COLOR_PRICE = 2500
DEFAULT_CUSTOM_ROLE_PRICE = 10000
DEFAULT_CUSTOM_ROLE_EDIT_PRICE = 1000
CUSTOM_ROLE_PERIOD = 7 * 86400
DEFAULT_RAIN_MIN = 100
DEFAULT_RAIN_MAX_PEOPLE = 10
DEFAULT_RAIN_WINDOW_MINUTES = 15
DEFAULT_SHOUTOUT_PRICE = 2500
DEFAULT_PIN_PRICE = 5000
DEFAULT_PIN_HOURS = 24
DEFAULT_AUCTION_MAX_HOURS = 72
DEFAULT_BOUNTY_DAYS = 7
MAX_RENTAL_DAYS = 30
MAX_TITLE_LENGTH = 32
MAX_BADGE_LENGTH = 64
MAX_ROLE_NAME_LENGTH = 32
MAX_SHOUTOUT_LENGTH = 500
MAX_TASK_LENGTH = 200
MAX_OPEN_BOUNTIES = 25
MAX_OPEN_AUCTIONS = 25
SINK_LOOP_SECONDS = 60

MAX_LEDGER_ENTRIES = 500
MAX_AMOUNT = 10**15
TOP_LIMIT = 10
SHOP_PAGE_SIZE = 8
REDEEM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REDEEM_CODE_GROUPS = 3
REDEEM_CODE_GROUP_SIZE = 4

GOLD = discord.Color.gold()
NIGHTLIFE_COLOR = discord.Color.purple()

GLOBAL_DEFAULTS = {
    "balances": {},
    "ledger": [],
    "next_tx": 1,
    "api_enabled": False,
    "api_host": DEFAULT_API_HOST,
    "api_port": DEFAULT_API_PORT,
    "api_tokens": {},
    "daily_amount": DEFAULT_DAILY_AMOUNT,
    "daily_cooldown": DEFAULT_DAILY_COOLDOWN,
    "weekly_amount": DEFAULT_WEEKLY_AMOUNT,
    "weekly_cooldown": DEFAULT_WEEKLY_COOLDOWN,
    "monthly_amount": DEFAULT_MONTHLY_AMOUNT,
    "monthly_cooldown": DEFAULT_MONTHLY_COOLDOWN,
    "annual_amount": DEFAULT_ANNUAL_AMOUNT,
    "annual_cooldown": DEFAULT_ANNUAL_COOLDOWN,
    "work_amount": DEFAULT_WORK_AMOUNT,
    "work_min": DEFAULT_WORK_MIN,
    "work_max": DEFAULT_WORK_MAX,
    "work_cooldown": DEFAULT_WORK_COOLDOWN,
    "streak_bonus_per_day": DEFAULT_STREAK_BONUS_PER_DAY,
    "streak_bonus_cap": DEFAULT_STREAK_BONUS_CAP,
    "streak_saver_price": DEFAULT_STREAK_SAVER_PRICE,
    "claim_booster_price": DEFAULT_CLAIM_BOOSTER_PRICE,
    "claim_booster_hours": DEFAULT_CLAIM_BOOSTER_HOURS,
    "claim_booster_multiplier": DEFAULT_CLAIM_BOOSTER_MULTIPLIER,
    "xp_booster_price": DEFAULT_XP_BOOSTER_PRICE,
    "xp_booster_hours": DEFAULT_XP_BOOSTER_HOURS,
    "xp_booster_multiplier": DEFAULT_XP_BOOSTER_MULTIPLIER,
    "wealth_tax_percent": DEFAULT_WEALTH_TAX_PERCENT,
    "wealth_tax_threshold": DEFAULT_WEALTH_TAX_THRESHOLD,
    "wealth_tax_cap": DEFAULT_WEALTH_TAX_CAP,
    "wealth_tax_day": 0,
    "casino_enabled": True,
    "casino_min_bet": DEFAULT_CASINO_MIN_BET,
    "casino_max_bet": DEFAULT_CASINO_MAX_BET,
    "casino_rake_percent": DEFAULT_CASINO_RAKE_PERCENT,
    "casino_50k_limit_applied": False,
    "holdem_settlements": {},
    "casino_exclusions": {},
    "nightlife_players": {},
    "nightlife_disabled_guilds": [],
    "claims": {},
    "shops": {},
    "inventories": {},
    "redeem_codes": {},
    "shop_channels": {},
    "shop_messages": {},
    "log_channels": {},
    "lwdmillions": {
        "enabled": True,
        "ticket_price": DEFAULT_LWDMILLIONS_TICKET_PRICE,
        "seed_jackpot": DEFAULT_LWDMILLIONS_SEED_JACKPOT,
        "jackpot": DEFAULT_LWDMILLIONS_SEED_JACKPOT,
        "jackpot_contribution_percent": DEFAULT_LWDMILLIONS_JACKPOT_CONTRIBUTION_PERCENT,
        "draw_number": 1,
        "next_draw": 0,
        "beacon_round": 0,
        "proof_version": 2,
        "secret": "",
        "commitment": "",
        "next_ticket_id": 1,
        "tickets": [],
        "history": [],
        "announcement_channels": {},
    },
}

GUILD_DEFAULTS = {
    "pay_tax_percent": DEFAULT_PAY_TAX_PERCENT,
    "house_cut_percent": DEFAULT_HOUSE_CUT_PERCENT,
    "perks_enabled": True,
    "title_price": DEFAULT_TITLE_PRICE,
    "badge_price": DEFAULT_BADGE_PRICE,
    "color_price": DEFAULT_COLOR_PRICE,
    "custom_roles_enabled": False,
    "custom_role_price": DEFAULT_CUSTOM_ROLE_PRICE,
    "custom_role_edit_price": DEFAULT_CUSTOM_ROLE_EDIT_PRICE,
    "custom_role_anchor": None,
    "rentable_roles": {},
    "rain_min": DEFAULT_RAIN_MIN,
    "rain_max_people": DEFAULT_RAIN_MAX_PEOPLE,
    "rain_window_minutes": DEFAULT_RAIN_WINDOW_MINUTES,
    "shoutout_channel": None,
    "shoutout_price": DEFAULT_SHOUTOUT_PRICE,
    "pin_channels": [],
    "pin_price": DEFAULT_PIN_PRICE,
    "pin_hours": DEFAULT_PIN_HOURS,
    "auctions_enabled": True,
    "auction_max_hours": DEFAULT_AUCTION_MAX_HOURS,
    "bounty_days": DEFAULT_BOUNTY_DAYS,
    "bounties": {},
    "next_bounty": 1,
    "auctions": {},
    "next_auction": 1,
    "pins": {},
}

MEMBER_DEFAULTS = {
    "title": None,
    "badge": None,
    "color": None,
    "custom_role": {},
    "rentals": {},
}

USER_DEFAULTS = {
    "boosters": {},
    "streak_savers": 0,
}
