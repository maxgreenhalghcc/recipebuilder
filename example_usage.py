"""Example usage of the personalized cocktail recipe engine."""
from __future__ import annotations

from recipebuilder import (
    FlavourAssociationModel,
    FlavourAssociationObservation,
    generate_cocktail_recipe,
    load_training_samples,
    save_model_weights,
    train_model_from_samples,
)


if __name__ == "__main__":
    seed_model = FlavourAssociationModel.from_file("data/flavour_associations.json")
    seed_model.train(
        [
            FlavourAssociationObservation(
                tags=["citrus", "herbal", "floral"],
                rating=0.9,
                role_ratios={"base": 0.45, "modifier": 0.3, "sweetener": 0.15, "juice": 0.1},
            )
        ]
    )

    samples = load_training_samples("data/training/successful_cocktails")
    model = train_model_from_samples(samples, base_model=seed_model, rating_floor=0.2)
    save_model_weights(model, "data/training/latest_weights.json")

    responses = {
        "base_spirit": "Gin",
        "season": "summer",
        "house_type": "modern house",
        "dining_style": "refreshing and vibrant flavours which awaken my senses",
        "music_preference": "jazz/blues",
        "modifier_question": "emerald",
        "sweetener_question": "floral",
        "aroma_preference": "citrus",
        "favourite_dessert": "fresh fruit",
        "notes": "Guest enjoys bright citrus and a touch of floral sweetness.",
    }

    recipe = generate_cocktail_recipe(
        responses,
        bar_id="cross_axes",
        association_model=model,
    )

    print(f"Recipe: {recipe.name}")
    print(f"Glassware: {recipe.glassware} with {recipe.ice}")
    print("Ingredients:")
    for suggestion in recipe.ingredients:
        amount = f"{suggestion.amount_ml:.0f} ml" if suggestion.amount_ml else "to taste"
        print(f"  - {amount} {suggestion.ingredient.name} ({suggestion.role})")
    if recipe.garnish:
        print(f"Garnish: {recipe.garnish}")
    print("Steps:")
    for index, step in enumerate(recipe.steps, start=1):
        print(f"  {index}. {step}")

    print("\nFlavour profile weighting:")
    for flavour, weight in recipe.flavour_profile:
        print(f"  {flavour}: {weight:.2f}")

    if recipe.explanations:
        print("\nModel reasoning:")
        for line in recipe.explanations:
            print(f"  - {line}")
