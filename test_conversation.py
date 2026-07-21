from conversation import handle_message, ACTIVE, AWAITING_CALORIE_GOAL


class FakeUser:
    """Stand-in for a User row. Just needs the same attributes."""
    def __init__(self):
        self.onboarding_state = "new"
        self.calorie_goal = None
        self.protein_goal = None


def test_full_onboarding_flow():
    user = FakeUser()

    # Start the conversation
    reply = handle_message(user, "start")
    assert user.onboarding_state == AWAITING_CALORIE_GOAL
    assert reply == "Welcome! What is your daily calorie goal?"

    # Provide calorie goal
    reply = handle_message(user, "2000")
    assert user.calorie_goal == 2000
    assert user.onboarding_state == "awaiting_protein_goal"
    assert reply == "Great. What is your daily protein goal in grams?"

    # Provide protein goal
    reply = handle_message(user, "150")
    assert user.protein_goal == 150
    assert user.onboarding_state == ACTIVE
    assert (
        reply
        == "You're set: 2,000 kcal and 150g protein daily.\nSend a meal photo whenever you eat."
    )

def test_bad_input_does_not_advance():
      u = FakeUser()
      handle_message(u, "start")

      assert "number" in handle_message(u, "banana")
      assert u.onboarding_state == AWAITING_CALORIE_GOAL
      assert u.calorie_goal is None

def test_non_positive_input_does_not_advance():
    user = FakeUser()
    handle_message(user, "start")

    for invalid_goal in ("0", "-200"):
        reply = handle_message(user, invalid_goal)

        assert reply == "Please send a number, like 1800."
        assert user.onboarding_state == AWAITING_CALORIE_GOAL
        assert user.calorie_goal is None
