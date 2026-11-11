class EconomyError(Exception):
    pass


class EconomyCodeNotFound(EconomyError):
    pass


class EconomyCodeRedeemed(EconomyError):
    pass
