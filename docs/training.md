# Training the Flavour Association Model

The recipe engine can now be tuned with real-world service data so that it
rewards flavour combinations and ratios that have proven successful with your
customers. This guide explains how to capture the right information, convert it
into training observations, and persist the updated model for reuse.

## 1. Capture cocktail outcome logs

Record each finished cocktail that guests respond positively to. For every
entry you should store:

- `name`: the drink served.
- `success_score`: how strong the guest feedback was (any consistent 1–5 or
  0–10 scale works).
- `max_score`: the top end of the scoring scale (used to normalise ratings).
- `tags`: optional free-form flavour descriptors that summarise the drink.
- `ingredients`: the components poured, including their role in the recipe,
  the amount in millilitres, and any flavour tags that describe their profile.

A compact JSON representation might look like:

```json
{
  "name": "Summer Garden Highball",
  "success_score": 4.9,
  "max_score": 5,
  "tags": ["refreshing", "herbal", "citrus"],
  "ingredients": [
    {"name": "Cross Axes Gin", "role": "base", "amount_ml": 50, "flavour_tags": ["botanical", "citrus", "juniper"]},
    {"name": "Elderflower Liqueur", "role": "modifier", "amount_ml": 20, "flavour_tags": ["floral", "sweet"]},
    {"name": "Lime Cordial", "role": "sweetener", "amount_ml": 15, "flavour_tags": ["citrus", "zesty"]},
    {"name": "Soda Water", "role": "juice", "amount_ml": 80, "flavour_tags": ["sparkling", "neutral"]}
  ]
}
```

Collect multiple entries like this in a file (for example
`data/training/successful_cocktails.json`).

## 2. Convert logs into training observations

Use `recipebuilder.training.load_training_samples` to read the JSON file and
`recipebuilder.training.build_observations_from_samples` to convert the service
logs into `FlavourAssociationObservation` objects. Low-rated samples can be
filtered out by raising the `rating_floor` value.

```python
from recipebuilder import load_training_samples, build_observations_from_samples

samples = load_training_samples("data/training/successful_cocktails.json")
observations = build_observations_from_samples(samples, rating_floor=0.3)
```

If you want to archive the derived observations for future reuse, call
`save_observations_to_file(observations, "data/flavour_associations.json")`.

## 3. Train or fine-tune a model

Start from the default association knowledge or a previously saved weight file
and update it with the new observations:

```python
from recipebuilder import FlavourAssociationModel, train_model_from_samples

seed = FlavourAssociationModel.from_file("data/flavour_associations.json")
model = train_model_from_samples(samples, base_model=seed, rating_floor=0.2)
```

The resulting `model` can be passed directly into
`generate_cocktail_recipe(..., association_model=model)` to influence recipe
selection and pour balancing.

## 4. Persist trained weights

To avoid retraining on every run, persist the learned weights with
`save_model_weights` and reload them later using `load_model_weights`:

```python
from recipebuilder import save_model_weights, load_model_weights

save_model_weights(model, "data/training/latest_weights.json")
model = load_model_weights("data/training/latest_weights.json")
```

This serialises the internal flavour and ratio biases, making it fast to
bootstrap the engine with your venue-specific knowledge.

## 5. Iterate regularly

Repeat the logging and training process as new cocktails are evaluated. Over
 time the engine will increasingly favour the flavour pairings and ratios that
perform best with your audience, leading to smarter bespoke recipes.
