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
    "condom": {"price": 75, "description": "Single-use protection."},
    "viagra": {"price": 400, "description": "For your next encounter.", "satisfaction": 20},
    "lube": {"price": 150, "description": "A little extra comfort.", "satisfaction": 10},
    "energy": {"price": 200, "description": "Restores stamina.", "restore": 30},
    "weed": {"price": 300, "description": "A mellow night. Leaves you tired.", "satisfaction": 12, "fatigue": 5},
    "cocaine": {"price": 1200, "description": "An expensive rush. A rough comedown.", "satisfaction": 30, "fatigue": 20},
    "ecstasy": {"price": 800, "description": "Party tonight, crash later.", "satisfaction": 25, "fatigue": 15},
    "shrooms": {"price": 600, "description": "A strange trip. Leaves you drained.", "satisfaction": 18, "fatigue": 10},
    "poppers": {"price": 250, "description": "A quick rush.", "satisfaction": 15, "fatigue": 5},
    "champagne": {"price": 500, "description": "Something to celebrate with.", "satisfaction": 12},
    "flowers": {"price": 180, "description": "Make an impression.", "satisfaction": 5},
    "perfume": {"price": 350, "description": "Smell your best.", "satisfaction": 8},
    "chocolate": {"price": 125, "description": "Something sweet.", "satisfaction": 6},
    "coffee": {"price": 100, "description": "A quick pick-me-up.", "restore": 15},
    "snack": {"price": 60, "description": "Grab a bite.", "restore": 10},
}
ITEM_ALIASES = {
    "condoms": "condom", "cannabis": "weed", "marijuana": "weed",
    "coke": "cocaine", "mdma": "ecstasy", "mushrooms": "shrooms",
    "chocolates": "chocolate", "snacks": "snack",
}
DISEASES = {
    "chlamydia": {"name": "Chlamydia", "treatment": 600, "penalty": 10},
    "gonorrhea": {"name": "Gonorrhea", "treatment": 1500, "penalty": 20},
    "syphilis": {"name": "Syphilis", "treatment": 3000, "penalty": 30},
    "herpes": {"name": "Genital herpes", "treatment": 4000, "penalty": 25},
    "hpv": {"name": "HPV", "treatment": 3500, "penalty": 15},
    "trichomoniasis": {"name": "Trichomoniasis", "treatment": 900, "penalty": 10},
    "pubic_lice": {"name": "Pubic lice", "treatment": 450, "penalty": 5},
    "scabies": {"name": "Scabies", "treatment": 750, "penalty": 10},
}
TEST_PRICE = 150
STAMINA_INTERVAL = 180  # One point every three minutes.
ENCOUNTER_STAMINA = 25
ENCOUNTER_COOLDOWN = 300
INCUBATION_SECONDS = 600
MAX_INVENTORY = 100
CONDOM_BREAK_PERCENT = 2
UNPROTECTED_RISK = 2

# These odds, bills and durations are balance settings, not medical estimates.
SUBSTANCE_CONSEQUENCES = {
    "weed": {"recovery": 600, "risk": 3, "bill": 800,
             "aftermath": "Weed left you foggy and exhausted."},
    "cocaine": {"recovery": 1800, "risk": 12, "bill": 3000,
                "aftermath": "The cocaine rush ended in a hard crash."},
    "ecstasy": {"recovery": 1200, "risk": 8, "bill": 2000,
                "aftermath": "Ecstasy left you drained after the party."},
    "shrooms": {"recovery": 900, "risk": 6, "bill": 1500,
                "aftermath": "The shrooms left you shaken and worn out."},
    "poppers": {"recovery": 600, "risk": 5, "bill": 1800,
                "aftermath": "The poppers left you dizzy with a pounding headache."},
    "champagne": {"recovery": 600, "risk": 3, "bill": 1000,
                  "aftermath": "The champagne left you with a hangover."},
}
HOSPITAL_RECOVERY = 3600
MIXED_SUBSTANCE_BILL = 5000


def resolve_aftermath(boosts: list[str], draw) -> dict:
    """Return persistent consequences for the substances used in this encounter."""
    substances = [key for key in SUBSTANCE_CONSEQUENCES if key in boosts]
    result = {"recovery": 0, "bill": 0, "hospital": False, "lines": []}
    if not substances:
        return result
    result["lines"] = [SUBSTANCE_CONSEQUENCES[key]["aftermath"] for key in substances]
    result["recovery"] = max(SUBSTANCE_CONSEQUENCES[key]["recovery"] for key in substances)
    if "poppers" in boosts and "viagra" in boosts:
        result.update(
            recovery=HOSPITAL_RECOVERY * 2, bill=MIXED_SUBSTANCE_BILL, hospital=True,
            lines=["Poppers and Viagra caused your blood pressure to crash. You collapsed and ended up in hospital."],
        )
        return result
    risk = min(60, sum(SUBSTANCE_CONSEQUENCES[key]["risk"] for key in substances) + 10 * (len(substances) - 1))
    if draw(100) < risk:
        names = ", ".join(substances)
        bill = max(SUBSTANCE_CONSEQUENCES[key]["bill"] for key in substances) + 500 * (len(substances) - 1)
        result.update(
            recovery=HOSPITAL_RECOVERY, bill=bill, hospital=True,
            lines=[f"You had a bad reaction after using {names} and ended up in hospital."],
        )
    else:
        result["recovery"] += 300 * (len(substances) - 1)
    return result


def new_profile(now: int) -> dict:
    return {
        "stamina": 100,
        "stamina_at": now,
        "last_encounter": None,
        "inventory": {},
        "boosts": [],
        "infections": {},
        "encounters": 0,
        "satisfaction": 0,
        "spent": 0,
        "recovery_until": 0,
        "recovery_reason": "",
        "medical_bill": 0,
        "hospital_visits": 0,
    }


def refreshed_profile(profile: dict | None, now: int) -> dict:
    result = new_profile(now)
    result.update(copy.deepcopy(profile or {}))
    result.pop("opted_in", None)  # Discard the retired participation flag on old profiles.
    if now >= result["recovery_until"]:
        result["recovery_until"] = 0
        result["recovery_reason"] = ""
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
    item = item.strip().lower()
    item = ITEM_ALIASES.get(item, item)
    cost = 0
    inventory = player["inventory"]
    if action in ("encounter", "use") and player["recovery_until"] > now:
        raise ValueError(f"You are recovering. Rest until <t:{player['recovery_until']}:R>.")
    if action == "buy":
        if item not in ITEMS:
            raise ValueError("That item is not available. Visit `sex shop`.")
        if not 1 <= quantity <= MAX_INVENTORY:
            raise ValueError(f"Quantity must be between 1 and {MAX_INVENTORY}.")
        count = inventory.get(item, 0) + quantity
        if count > MAX_INVENTORY:
            raise ValueError(f"You can hold at most {MAX_INVENTORY} of each item.")
        cost = ITEMS[item]["price"] * quantity
        inventory[item] = count
        message = f"Bought {quantity}x {item}."
    elif action == "use":
        if item == "condom":
            raise ValueError("Condoms are used automatically during sex.")
        if item not in ITEMS:
            raise ValueError("That item is not available. Visit `sex shop`.")
        if inventory.get(item, 0) < 1:
            raise ValueError("You do not own that item. Visit `sex shop`.")
        if ITEMS[item].get("restore"):
            if player["stamina"] == 100:
                raise ValueError("Your stamina is already full.")
            restored = min(ITEMS[item]["restore"], 100 - player["stamina"])
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
        if player["medical_bill"]:
            raise ValueError(f"You owe {player['medical_bill']:,} LWD$ in hospital bills. Pay with `sex clinic pay` first.")
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
        score += sum(ITEMS.get(key, {}).get("satisfaction", 0) for key in player["boosts"])
        fatigue = sum(ITEMS.get(key, {}).get("fatigue", 0) for key in player["boosts"])
        score = max(0, min(100, score - penalty))
        broken = protected and draw(100) < CONDOM_BREAK_PERCENT
        susceptible = [key for key in DISEASES if key not in player["infections"]]
        if (not protected or broken) and susceptible and draw(100) < UNPROTECTED_RISK:
            key = susceptible[draw(len(susceptible))]
            player["infections"][key] = {"detectable_at": now + INCUBATION_SECONDS, "diagnosed": False}
        if protected:
            inventory["condom"] -= 1
        aftermath = resolve_aftermath(player["boosts"], draw)
        player["boosts"] = []
        player["stamina"] = max(0, player["stamina"] - ENCOUNTER_STAMINA - fatigue)
        if aftermath["recovery"]:
            player["recovery_until"] = now + aftermath["recovery"]
            player["recovery_reason"] = "Hospital recovery" if aftermath["hospital"] else "Comedown"
        if aftermath["hospital"]:
            player["stamina"] = 0
            player["stamina_at"] = now
            player["medical_bill"] += aftermath["bill"]
            player["hospital_visits"] += 1
            score = 0
        player["last_encounter"] = now
        player["encounters"] += 1
        player["satisfaction"] += score
        message = (
            f"You had sex with {ESCORTS[escort]['name']} at the {venue}.\n"
            f"{'Your condom broke!' if broken else 'Condom held.' if protected else 'No condom used.'}"
        )
        if aftermath["lines"]:
            message += "\n" + "\n".join(aftermath["lines"])
            message += f"\nRest until <t:{player['recovery_until']}:R>."
        if aftermath["bill"]:
            message += f"\nHospital bill: {aftermath['bill']:,} LWD$. Pay with `sex clinic pay`."
    elif action == "pay":
        cost = player["medical_bill"]
        if not cost:
            raise ValueError("You have no hospital bills to pay.")
        player["medical_bill"] = 0
        message = "Hospital bill paid."
    elif action == "test":
        cost = TEST_PRICE
        found = []
        for key, infection in player["infections"].items():
            if now >= infection["detectable_at"]:
                infection["diagnosed"] = True
                found.append(DISEASES[key]["name"])
        message = "Clinic result: " + (", ".join(found) if found else "negative") + "."
    elif action == "cure":
        diagnosed = [key for key, infection in player["infections"].items() if infection["diagnosed"]]
        if not diagnosed:
            raise ValueError("No diagnosed conditions to treat. Get a clinic test first.")
        cost = sum(DISEASES[key]["treatment"] for key in diagnosed)
        for key in diagnosed:
            del player["infections"][key]
        message = "Treated: " + ", ".join(DISEASES[key]["name"] for key in diagnosed) + "."
    else:
        raise ValueError("Unknown Nightlife action.")

    if balance < cost:
        raise ValueError(f"Insufficient funds. You need {cost:,} LWD$.")
    player["spent"] += cost
    return player, balance - cost, message, cost
