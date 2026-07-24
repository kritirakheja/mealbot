import base64
from enum import Enum
from pathlib import Path

from google import genai
from pydantic import BaseModel, Field, model_validator


class ImageQuality(str, Enum):
    CLEAR = "clear"
    USABLE = "usable"
    UNUSABLE = "unusable"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


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


class MealImageAnalysis(BaseModel):
    image_quality: ImageQuality
    visible_foods: list[str]
    dish_candidates: list[str] = Field(max_length=3)
    dish_confidence: Confidence
    portion_confidence: Confidence
    assumptions: list[str]
    ambiguity_reason: str | None = None
    estimate: NutritionEstimate | None = None


NUTRITION_PROMPT = """
Inspect this meal photo for a nutrition logging application.

Return:
- image_quality: clear, usable, or unusable
- visible_foods: only foods visibly supported by the image
- dish_candidates: up to three plausible dish names
- dish_confidence: confidence in the best dish identification
- portion_confidence: confidence in the visible serving size
- assumptions: every assumption needed to estimate nutrition
- ambiguity_reason: why the image is uncertain, or null
- estimate: nutrition ranges, or null when a responsible estimate
  cannot be made

Rules:
- Mark the image unusable if blur, darkness, obstruction, or framing
  prevents reliable food identification.
- Do not invent hidden ingredients or serving sizes.
- Use ranges rather than exact nutrition values.
- Wider uncertainty must produce wider ranges.
- Return estimate=null for an unusable image.
- Do not provide medical advice, dieting prescriptions, moral judgments,
  or suggestions that the user should eat more or less.
- Return only JSON matching the supplied schema.
"""

class NutritionError(Exception):
    """Raised when Gemini cannot produce a valid nutrition estimate."""

def analyze_meal_image(
    image_path: Path,
    mime_type: str,
) -> MealImageAnalysis:

    with image_path.open("rb") as f:
        image_bytes = f.read()

    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    client = genai.Client()

    try:

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
              "schema": MealImageAnalysis.model_json_schema(),
          },
      )

      return MealImageAnalysis.model_validate_json(interaction.output_text)

    except Exception as e:
      raise NutritionError(f"Failed to analyze meal image: {e}") from e


def format_range(nutrient_range: NutritionRange, unit: str) -> str:
    return (
        f"{nutrient_range.minimum:.0f}–"
        f"{nutrient_range.maximum:.0f}{unit}"
    )


def format_nutrition_reply(
    estimate: NutritionEstimate,
    meal_type: str,
) -> str:
    foods = ", ".join(item.name for item in estimate.food_items)

    return (
        f"{meal_type.title()} logged: {foods}\n\n"
        "Estimated nutrition:\n"
        f"• Calories: {format_range(estimate.total_calories_kcal, ' kcal')}\n"
        f"• Protein: {format_range(estimate.total_protein_g, 'g')}\n"
        f"• Carbs: {format_range(estimate.total_carbs_g, 'g')}\n"
        f"• Fat: {format_range(estimate.total_fat_g, 'g')}\n"
        f"• Fibre: {format_range(estimate.total_fibre_g, 'g')}"
    )
