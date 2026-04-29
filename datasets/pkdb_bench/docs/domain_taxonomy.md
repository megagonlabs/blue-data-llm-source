# PKDB-Bench Domain Taxonomy

**Status:** operational guide, 2026-04-28.

**Purpose.** This file defines the domain-family vocabulary curators should use
when setting `draft.json::domain`. It is intentionally lighter than a schema
enum: the field remains free text, but new instances should prefer the
`Family/Subdomain` pattern below.

Use `benchmark_domain_survey.md` for literature motivation. Use this file for
day-to-day curation and audit.

## Rules

1. Prefer `Family/Subdomain`, for example `Science/Astronomy` or
   `Culture/Literature`.
2. Use the closest family. Do not invent a new top-level family unless none fits.
3. Keep subdomains short and natural. They are not a fixed enum.
4. Existing legacy labels do not need repair only because their family prefix is
   older. Repair only when the domain is missing, misleading, or too vague for
   planning.
5. Domain labels support coverage planning; they do not decide row correctness.
   A domain-label issue is usually non-blocking hygiene, not a registration
   blocker, unless the value is absent or actively misleading.

## Domain Families

| Family | Use for | Example labels |
| --- | --- | --- |
| `Geography` | Places, borders, regions, physical geography, cities, rivers, mountains, climate-as-place. | `Geography/Hydrology`, `Geography/Mountaineering`, `Geography/Climate` |
| `Politics` | Governments, elections, international relations, states as political entities, geopolitics. | `Politics/Geopolitics`, `Politics/Government`, `Politics/International Relations` |
| `Law` | Legal systems, courts, statutes, landmark cases, rights, legal doctrines, and public-policy rules when law is primary. | `Law/Courts`, `Law/Constitutional Law`, `Law/Public Policy` |
| `History` | Historical events, periods, leaders, empires, adoption dates, historically framed entities. | `History/Events`, `History/Politics`, `History/Technology` |
| `Business` | Companies, industries, markets, trade, management, finance, accounting, economic organizations. | `Business/Companies`, `Business/Finance`, `Business/Economics` |
| `Science` | Natural sciences, scientific disciplines, scientific awards, broadly knowable technical facts. | `Science/Astronomy`, `Science/Biology`, `Science/Academia` |
| `Technology` | Inventions, computing, engineering artifacts, software, consumer technology, infrastructure technology. | `Technology/Inventions`, `Technology/Computing`, `Technology/Engineering` |
| `Health` | Medicine, public health, nutrition, human biology when framed as health knowledge. | `Health/Medicine`, `Health/Nutrition`, `Health/Public Health` |
| `Culture` | Arts, literature, film, music, games, entertainment, media, cultural works. | `Culture/Film`, `Culture/Music`, `Culture/Literature` |
| `Religion` | Religions, holy sites, mythology, theology, religious institutions and figures. | `Religion/Holy Sites`, `Religion/Mythology`, `Religion/Institutions` |
| `Sports` | Sports, leagues, competitions, athletes, records, teams. | `Sports/Football`, `Sports/Olympics`, `Sports/Records` |
| `Education` | Universities, schools, academic institutions, education systems, credentials. | `Education/Universities`, `Education/Institutions` |
| `Society` | Demographics, languages, population groups, social indicators, everyday social facts. | `Society/Languages`, `Society/Demographics`, `Society/Population` |
| `Food` | Foods, dishes, cuisines, nutrients when framed as common food knowledge. | `Food/Nutrition`, `Food/Cuisine`, `Food/Dishes` |
| `Transportation` | Airports, railways, roads, ports, vehicles, transit systems. | `Transportation/Airports`, `Transportation/Rail`, `Transportation/Roads` |
| `Environment` | Energy, conservation, natural hazards, climate as environmental system, ecological facts. | `Environment/Energy`, `Environment/Climate`, `Environment/Conservation` |

## Choosing Between Neighboring Families

- Use `Geography/*` when the table is primarily about places or spatial
  membership, even if political attributes appear as columns.
- Use `Politics/*` when the row universe is political institutions, states,
  governments, conflicts, memberships, or international organizations.
- Use `Law/*` when the table is primarily about legal systems, courts, cases,
  statutes, rights, legal doctrines, or public-policy rules. Use `Politics/*`
  when the main entity is government, political membership, or international
  relations rather than legal doctrine or legal status.
- Use `History/*` when the query is anchored by a historical period or event.
- Use `Science/*` for knowledge about scientific objects, disciplines, awards,
  and broadly known science facts.
- Use `Health/*` when the framing is medical, nutritional, or public-health
  oriented. Use `Food/*` when the framing is dishes/cuisines/ingredients.
- Use `Culture/*` for entertainment and arts; legacy `Entertainment/*` labels
  are acceptable but new instances should prefer `Culture/*`.
- Use `Business/*` for economics/finance/company content unless the table is
  primarily a political-economy membership or policy instance.

## Audit Guidance

When auditing metadata:

- **Missing `domain`:** structural issue; registration should not proceed until
  populated.
- **Actively misleading `domain`:** structural issue; usually non-blocking
  hygiene if the rest of the metadata is correct, but can become blocking when
  it would mislead benchmark stratification.
- **Legacy but understandable label:** suggestion at most; do not block.
- **Overly broad label:** non-blocking hygiene when a clear `Family/Subdomain`
  replacement exists.

## Current Coverage Notes

The current benchmark is geography-heavy. When candidate quality is otherwise
similar, prefer underrepresented families such as `Science/Astronomy`,
`Religion/*`, `Technology/*`, `Food/*`, `Business/*`, `Law/*`,
`Culture/Literature`, `Transportation/*`, and `Environment/Energy`.
