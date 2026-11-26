class TargetingError(Exception):
    pass


class ArticleNotFound(TargetingError):
    pass


class ComboNotFound(TargetingError):
    pass


class InvalidCombo(TargetingError):
    pass


class InsufficientHistory(TargetingError):
    pass


class DataIncomplete(TargetingError):
    pass


class SingleClass(TargetingError):
    pass


class NotConverged(TargetingError):
    pass


class ModelMismatch(TargetingError):
    pass


class FutureModel(TargetingError):
    pass


class ModelNotReady(TargetingError):
    pass


class SaveFailed(TargetingError):
    pass


class UnsupportedCombo(TargetingError):
    """점수를 낼 수 없는 품목·색상. reason: "unknown" | "not_in_catalog" | "not_in_model" """

    REASONS = ("unknown", "not_in_catalog", "not_in_model")

    def __init__(self, message: str, reason: str):
        if reason not in self.REASONS:
            raise ValueError(f"reason은 {self.REASONS} 중 하나여야 합니다: {reason!r}")
        super().__init__(message)
        self.reason = reason
