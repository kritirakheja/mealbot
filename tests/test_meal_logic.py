from datetime import time

from meal_logic import infer_meal_type


def test_meal_type_boundaries():
    assert infer_meal_type(time(4, 59)) == "dinner"
    assert infer_meal_type(time(5, 0)) == "breakfast"
    assert infer_meal_type(time(10, 59)) == "breakfast"
    assert infer_meal_type(time(11, 0)) == "lunch"
    assert infer_meal_type(time(15, 59)) == "lunch"
    assert infer_meal_type(time(16, 0)) == "snack"
    assert infer_meal_type(time(17, 59)) == "snack"
    assert infer_meal_type(time(18, 0)) == "dinner"
