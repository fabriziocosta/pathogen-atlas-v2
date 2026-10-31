# How the V2 species map is computed

This document describes the data transformation and embedding behind the Phase 5 species feature-space plot and the interactive explorer. The map is an unsupervised, two-dimensional representation of species-level domain-expert profiles. It is not a text embedding: the IMSTE input is a table of numeric feature prevalences derived from scored abstracts.

## Data flow

The coordinate calculation starts from the Phase 4 release table `data/04_tables/release/species_feature_matrix.parquet`:

1. **Select evidence eligible for expert scoring.** Phase 4 takes Phase 3 species–PMID decisions accepted at the configured human-pathogen confidence threshold (currently `0.75`). Domain-expert decisions are joined to these accepted `(PMID, species)` pairs. An abstract that did not pass the Phase 3 gate does not contribute to the feature profile.
2. **Turn each expert probability into a yes/no answer.** For each question, Phase 4 uses `p_yes >= question_confidence_threshold` (currently `0.75`) as “yes” and a lower probability as “no”. Only completed decisions with a numeric probability are scored. Failed or missing decisions remain unanswered; they are not counted as “no”.
3. **Aggregate answers per species and question.** For species `s` and expert question `j`, let `n_answered(s,j)` be the number of scored abstracts and `n_yes(s,j)` the number whose answer is yes. The feature value is:

   ```text
   fraction_yes(s,j) = n_yes(s,j) / n_answered(s,j)
   ```

   This value ranges from 0 to 1. A value of 0 means the question was answered for the species and none of those answers passed the yes threshold. A missing value means there were no scored answers for that species-question pair. The wide feature matrix has one row per species with feature columns named `feature_<question_id>`.

The feature set and question wording are defined in `configs/domain_expert_questions.yaml`. The decision and aggregation thresholds are in `configs/notebook_parameters.yaml` under the Phase 3, Phase 4, and expert-decision settings.

## Preparing the matrix for IMSTE

`pathogen_atlas_v2/plots/embeddings.py` prepares the Phase 4 matrix as follows:

1. It uses every `feature_*` column and keeps the species row order from the feature matrix. The species names are identifiers, not input features.
2. Values are converted to numeric; invalid values become missing. Features that are entirely missing are removed.
3. Remaining missing cells are imputed with the mean of that feature across species. This is a neutral, mean-profile imputation for the geometry; it does not change the original feature values used to color the panels or explorer. In standardized coordinates, a mean-imputed cell is at zero on that feature axis.
4. Features with zero variance after imputation are removed because they cannot distinguish species.
5. Each remaining feature is standardized across species using scikit-learn's `StandardScaler`:

   ```text
   z(s,j) = (fraction_yes(s,j) - mean_j) / standard_deviation_j
   ```

   Consequently, the graph uses differences in standardized prevalence profiles. Without this step, features with greater raw spread would contribute more to Euclidean distance simply because of their scale.

The resulting matrix `Z` has one row per species and one column per variable expert feature. Its Euclidean distances are the dissimilarities used to build the graph:

```text
d(s,t) = sqrt(sum_j (z(s,j) - z(t,j))^2)
```

## IMSTE graph construction

V2 calls `IteratedMinimumSpanningTreeEmbedder.fit_transform(Z)` from the pinned IMSTE revision declared in `pyproject.toml`. The configured method is FAMST, which constructs an approximate sparse graph for efficient spanning-tree construction. It begins with approximate nearest-neighbor candidates, connects disconnected components using representative points and candidate bridges, then extracts successive minimum-spanning-tree structures. If a bottleneck edge has to be reused to keep later structures connected, its rank increases, which reduces its influence.

An edge first assigned rank `r` has inverse-rank weight `1/r`. Early, shorter relationships in the spanning-tree sequence therefore contribute more strongly than connections that appear only in later trees. The requested tree count is the number of tree iterations, not a guarantee that every edge is distinct in FAMST's approximate graph.

The Phase 5 settings currently used are:

| Setting | Value | Role |
| --- | ---: | --- |
| `imste_n_msts` | 64 | Number of ranked spanning-tree iterations. |
| `imste_n_epochs` | 1000 | Coordinate-optimization epochs. |
| `imste_mst_method` | `famst` | Approximate sparse FAMST graph construction. |
| `imste_mst_neighbors` | 15 | Configured initial candidate-neighbor count; FAMST may raise it to satisfy its graph requirements. |
| `imste_mst_inter_component_edges` | 5 | Bridge candidates considered for component connections. |
| `imste_mst_max_neighbors` | `null` | No explicit neighbor cap is configured. |
| `imste_mst_representatives_per_component` | 10 | Representative cap for FAMST component-bridge selection. |
| `imste_random_state` | 42 | Seed for initialization, graph sampling, and optimization. |

These values are set in `configs/notebook_parameters.yaml` in the `phase5_plot_creation` section. The full resolved configuration is also recorded in `data/05_plots/release/species_feature_space.metadata.json` when the plot is generated.

## Optimizing the 2D coordinates

IMSTE learns one 2D coordinate `y_s` per species directly. It does not first apply PCA, UMAP, t-SNE, or a neural text encoder. The objective balances:

- **Attraction:** points connected in the ranked graph are pulled together. For an embedded graph edge, the loss increases with its squared coordinate distance `d²`, using `log(1 + d² + epsilon)` and the edge's inverse-rank weight.
- **Repulsion:** sampled non-neighbor pairs that are too close are pushed apart. The soft margin loss is `softplus((logistic_margin - d²) / logistic_temperature)`.

The combined loss gives attraction and repulsion shares of `1 - lambda_rep` and `lambda_rep`, respectively. The currently configured optimizer values are:

| Setting | Value | Role |
| --- | ---: | --- |
| `imste_negative_ratio` | 5 | Non-neighbor samples per eligible endpoint of a positive edge. |
| `imste_lambda_rep` | 0.5 | Repulsion share; attraction also receives 0.5. |
| `imste_learning_rate` | 0.05 | Starting optimization step size, decayed over epochs. |
| `imste_batch_size` | 32768 | Maximum active-edge batch size for supported batched optimization; CPU execution ignores it. |
| `imste_epsilon` | 0.0001 | Smoothing constant in the attraction probability. |
| `imste_logistic_margin` | 1.0 | Squared-distance margin for repulsion. |
| `imste_logistic_temperature` | 0.5 | Softness of the repulsive margin. |
| `imste_device` | `auto` | Selects the available supported backend; `auto` uses the CPU path when no accelerator is requested. |

The implementation initializes coordinates with small random values, updates them over the requested epochs, and recenters the coordinates after each epoch. Recentering removes arbitrary global translation without changing pairwise distances. The random seed makes runs reproducible for the same software, configuration, input rows, and backend; approximate graph construction and floating-point/backend differences mean runs with changed inputs or runtime conditions need not have identical coordinates.

## What the axes and visible colors mean

The generated `species_feature_space_coordinates.parquet` stores the learned `imste_1` and `imste_2` values keyed by canonical species. Those coordinates are also used by the interactive explorer, so each species has one precomputed map position. Axis 1 and axis 2 are learned coordinates, not named biological gradients. Their signs, rotation, and scale have no standalone interpretation; the map is intended to show relative structure in the expert-feature profiles.

Coloring by an expert feature uses its observed, unstandardized `fraction_yes` value from the original feature matrix, from 0 to 1. Zero and unscored values are displayed in gray. This means the map geometry and the color encodings serve different purposes: standardized values determine distances and coordinates, while the original prevalence makes the selected feature's meaning readable. Taxonomy and Pathogenic Interest Index sizing are also display overlays; neither is used to compute the IMSTE coordinates.

## Outputs and reproducibility

Phase 5 writes the figure, coordinates, and a JSON metadata sidecar under `data/05_plots/release/`. The metadata records the estimator settings, feature columns used, number of species and features, coordinate path, and missing value treatment. Phase 5 reads the Phase 4 Parquet release and recomputes the embedding when run; it does not update coordinates incrementally. Changing the Phase 4 evidence, decision thresholds, expert questions, feature preprocessing, IMSTE version, or IMSTE settings can change the embedding. Rebuild the explorer data after regenerating the coordinates so the website and static plot use the same map positions.

## Code references

- `pathogen_atlas_v2/pipelines/04_build_tables.py` — evidence gate and Phase 4 release construction.
- `pathogen_atlas_v2/tables/feature_tables.py` — yes-answer aggregation and species feature matrix pivot.
- `pathogen_atlas_v2/plots/embeddings.py` — imputation, scaling, IMSTE fit, coordinate and metadata output.
- `pathogen_atlas_v2/pipelines/05_make_plots.py` — Phase 5 input loading and configuration forwarding.
- `configs/notebook_parameters.yaml` — current thresholds and Phase 5 settings.
