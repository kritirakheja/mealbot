import base64
from pathlib import Path
from typing import Literal

from google import genai
from pydantic import BaseModel, Field, model_validator


class NutritionRange(BaseModel):
    minimum: float = Field(ge=0)
    maximum: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_range(self):
        if self.minimum > self.maximum:
            raise ValueError("Minimum cannot be greater than maximum")
        return self


class FoodItem(BaseModel):
    name: str
    portion_description: str
    calories_kcal: NutritionRange
    protein_g: NutritionRange
    carbs_g: NutritionRange
    fat_g: NutritionRange
    fibre_g: NutritionRange


class NutritionEstimate(BaseModel):
    food_items: list[FoodItem]
    total_calories_kcal: NutritionRange
    total_protein_g: NutritionRange
    total_carbs_g: NutritionRange
    total_fat_g: NutritionRange
    total_fibre_g: NutritionRange
    confidence: Literal["low", "medium", "high"]


NUTRITION_PROMPT = """
Analyze this meal photo for a calorie-tracking app.

- Identify only food visibly supported by the image.
- Estimate portions conservatively.
- Return calories, protein, carbs, fat, and fibre as ranges, never exact values. Range width should match your uncertainty: narrow for clear portions, wide for ambiguous ones (hidden oil/ghee, sauces, unclear size).
- Set confidence to low for ambiguous meals.
- No medical, weight-loss, or moralizing advice.
- Return only JSON matching the supplied schema.
"""

def analyze_meal_image(
    image_path: Path,
    mime_type: str,
) -> NutritionEstimate:

    with image_path.open("rb") as f:
        image_bytes = f.read()

    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    client = genai.Client()

    interaction = client.interactions.create(
        model="gemini-flash-latest",
        input=[
            {
                "type": "image",
                "data": image_base64,
                "mime_type": mime_type,
            },
            {
                "type": "text",
                "text": NUTRITION_PROMPT,
            },
        ],
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": NutritionEstimate.model_json_schema(),
        },
    )

    return NutritionEstimate.model_validate_json(interaction.output_text)
