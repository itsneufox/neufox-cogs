"""Non-graphic, fictional adult nightlife game rules; no Discord dependencies.

Disease names are real; probabilities, progression and item effects are game mechanics.
Actions return a new profile and balance so rejected actions cannot spend funds.
"""

from __future__ import annotations

import copy
import secrets


ESCORTS = {
    "alex": {"name": "Alex", "price": 250, "charm": 8},
    "robin": {"name": "Robin", "price": 750, "charm": 16},
    "blair": {"name": "Blair", "price": 2000, "charm": 25},
}
VENUES = {
    "motel": {"price": 100, "bonus": 0},
    "hotel": {"price": 500, "bonus": 10},
    "penthouse": {"price": 2000, "bonus": 20},
}
ITEMS = {
    "condom": {"price": 75, "description": "Single-use protection. Automatically used for protected encounters."},
    "viagra": {"price": 400, "description": "Use to add 20 satisfaction to your next encounter."},
    "lube": {"price": 150, "description": "Use to add 10 satisfaction to your next encounter."},
    "energy": {"price": 200, "description": "Use to restore 30 stamina, up to 100."},
}
DISEASES = {
    "chlamydia": {"name": "Chlamydia", "treatment": 600, "penalty": 10},
    "gonorrhea": {"name": "Gonorrhea", "treatment": 1500, "penalty": 20},
    "syphilis": {"name": "Syphilis", "treatment": 3000, "penalty": 30},
}
TEST_PRICE = 150
STAMINA_INTERVAL = 180  # One point every three minutes.
ENCOUNTER_STAMINA = 25
ENCOUNTER_COOLDOWN = 300
INCUBATION_SECONDS = 600
MAX_INVENTORY = 100
CONDOM_BREAK_PERCENT = 2
UNPROTECTED_RISK = 2


def new_profile(now: int) -> dict:
    return {
        "opted_in": False,
        "stamina": 100,
        "stamina_at": now,
        "last_encounter": None,
        "inventory": {},
        "boosts": [],
        "infections": {},
        "encounters": 0,
        "satisfaction": 0,
        "spent": 0,
    }


def refreshed_profile(profile: dict | None, now: int) -> dict:
    result = new_profile(now)
    result.update(copy.deepcopy(profile or {}))
    elapsed = max(0, now - result["stamina_at"])
    gained = elapsed // STAMINA_INTERVAL
    result["stamina"] = min(100, result["stamina"] + gained)
    if result["stamina"] == 100:
        result["stamina_at"] = now
    else:
        result["stamina_at"] += gained * STAMINA_INTERVAL
    return result


def title_for(encounters: int) -> str:
    for threshold, title in ((100, "Nightlife Legend"), (25, "VIP Regular"), (10, "Regular")):
        if encounters >= threshold:
            return title
    return "Newcomer"


def apply_action(
    profile: dict | None,
    balance: int,
    action: str,
    *,
    now: int,
    item: str = "",
    quantity: int = 1,
    escort: str = "alex",
    venue: str = "motel",
    protected: bool = True,
    randbelow=None,
) -> tuple[dict, int, str, int]:
    """Validate and resolve an action without mutating the supplied state."""
    player = refreshed_profile(profile, now)
    draw = randbelow if randbelow is not None else secrets.randbelow
    cost = 0
    if action == "join":
        player["opted_in"] = True
        return player, balance, "You joined Nightlife. All NPCs are consenting adults; encounters happen off-screen.", 0
    if action == "leave":
        player["opted_in"] = False
        return player, balance, "You left Nightlife. Progress is saved; leaving does not refund spending or clear conditions.", 0
    if not player["opted_in"]:
        raise ValueError("Join first with `sex join confirm` to confirm you are 18+ and want to play.")

    inventory = player["inventory"]
    if action == "buy":
        if item not in ITEMS:
            raise ValueError("Choose a shop item: condom, viagra, lube, or energy.")
        if not 1 <= quantity <= MAX_INVENTORY:
            raise ValueError(f"Quantity must be between 1 and {MAX_INVENTORY}.")
        count = inventory.get(item, 0) + quantity
        if count > MAX_INVENTORY:
            raise ValueError(f"You can hold at most {MAX_INVENTORY} of each item.")
        cost = ITEMS[item]["price"] * quantity
        inventory[item] = count
        message = f"Bought {quantity}x {item}."
    elif action == "use":
        if item not in ("viagra", "lube", "energy"):
            raise ValueError("Use viagra, lube, or energy. Condoms are used automatically for protected encounters.")
        if inventory.get(item, 0) < 1:
            raise ValueError("You do not own that item. Visit `sex shop`.")
        if item == "energy":
            if player["stamina"] == 100:
                raise ValueError("Your stamina is already full.")
            restored = min(30, 100 - player["stamina"])
            player["stamina"] += restored
            if player["stamina"] == 100:
                player["stamina_at"] = now
            message = f"Restored {restored} stamina. Stamina: {player['stamina']}/100."
        else:
            if item in player["boosts"]:
                raise ValueError("That boost is already ready for your next encounter.")
            player["boosts"].append(item)
            message = f"{item.title()} boost ready for your next encounter."
        inventory[item] -= 1
    elif action == "encounter":
        if escort not in ESCORTS or venue not in VENUES:
            raise ValueError("Choose an escort from `sex escorts` and a venue: motel, hotel, or penthouse.")
        last = player["last_encounter"]
        if last is not None and now - last < ENCOUNTER_COOLDOWN:
            raise ValueError(f"Rest for {ENCOUNTER_COOLDOWN - (now - last)} more seconds.")
        if player["stamina"] < ENCOUNTER_STAMINA:
            raise ValueError("You need 25 stamina. Rest or use an energy item.")
        if protected and inventory.get("condom", 0) < 1:
            raise ValueError("A protected encounter needs a condom. Buy one with `sex buy condoms`.")
        cost = ESCORTS[escort]["price"] + VENUES[venue]["price"]
        if balance < cost:
            raise ValueError(f"Insufficient funds. You need {cost:,} LWD$.")
        penalty = sum(DISEASES[key]["penalty"] for key in player["infections"])
        score = 35 + draw(26) + ESCORTS[escort]["charm"] + VENUES[venue]["bonus"]
        score += 20 if "viagra" in player["boosts"] else 0
        score += 10 if "lube" in player["boosts"] else 0
        score = max(0, min(100, score - penalty))
        broken = protected and draw(100) < CONDOM_BREAK_PERCENT
        susceptible = [key for key in DISEASES if key not in player["infections"]]
        if (not protected or broken) and susceptible and draw(100) < UNPROTECTED_RISK:
            key = susceptible[draw(len(susceptible))]
            player["infections"][key] = {"detectable_at": now + INCUBATION_SECONDS, "diagnosed": False}
        if protected:
            inventory["condom"] -= 1
        player["boosts"] = []
        player["stamina"] -= ENCOUNTER_STAMINA
        player["last_encounter"] = now
        player["encounters"] += 1
        player["satisfaction"] += score
        message = (
            f"Your evening with {ESCORTS[escort]['name']} at the {venue} fades to black.\n"
            f"Satisfaction: {score}/100 • Stamina: {player['stamina']}/100 • {title_for(player['encounters'])}\n"
            f"{'Your condom broke!' if broken else 'Condom held.' if protected else 'Unprotected encounter.'} "
            "In-game conditions take 10 minutes to become detectable; visit the clinic to test."
        )
    elif action == "test":
        cost = TEST_PRICE
        found = []
        for key, infection in player["infections"].items():
            if now >= infection["detectable_at"]:
                infection["diagnosed"] = True
                found.append(DISEASES[key]["name"])
        message = "Clinic result: " + (", ".join(found) if found else "no in-game conditions detected") + "."
        message += " Conditions acquired in the last 10 minutes may not show yet."
    elif action == "cure":
        diagnosed = [key for key, infection in player["infections"].items() if infection["diagnosed"]]
        if not diagnosed:
            raise ValueError("No diagnosed conditions to treat. Get a clinic test first.")
        cost = sum(DISEASES[key]["treatment"] for key in diagnosed)
        for key in diagnosed:
            del player["infections"][key]
        message = "Treated: " + ", ".join(DISEASES[key]["name"] for key in diagnosed) + ". Undiagnosed conditions are unaffected."
    else:
        raise ValueError("Unknown Nightlife action.")

    if balance < cost:
        raise ValueError(f"Insufficient funds. You need {cost:,} LWD$.")
    player["spent"] += cost
    return player, balance - cost, message, cost
