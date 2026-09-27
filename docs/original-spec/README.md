# The original spec and build plan

These three documents were written on September 25, 2026, before any code, when Ignatius at Home was planned as an iPhone app for the Spiritual Exercises in daily life (a 19th Annotation retreat of about thirty weeks). They're copied here unchanged from the project's private planning repository (which also holds the retreat handouts, so it stays private).

| Document | What it covers |
| --- | --- |
| [design-spec.md](design-spec.md) | The product: who it's for, the daily lectio divina session (the reading, A Word for You, The Text Up Close, journaling), the screens, the voice guide and live director, voices and costs, the content pipeline, and rights and licensing |
| [technical-spec.md](technical-spec.md) | The production architecture as first planned: SwiftUI on iPhone, Google Cloud (Cloud Run, Firestore, Cloud Storage and CDN, Workflows), Claude on Vertex AI, the week bundle format, and the director's realtime voice |
| [staging-plan.md](staging-plan.md) | The build estimate and AI-assisted staging plan: workstreams and agent hours, stages with exit criteria, how Claude Code and Codex split the work, the test plan and the evaluations for the director |

**What happened next.** For the class project the plan was reshaped into a web app that anyone can use with their own material: a FastAPI backend on Render with Supabase, and a static site on GitHub Pages, built in a few days rather than weeks. Much of the original design survived: the day as lectio divina with the reading heard more than once, a reflection for the heart and a close reading of the text, the painting for each day, two voice tiers, a model that plans the days from a handout, public-domain art and scripture where rights are unclear, and a spoken companion that listens more than it advises. What changed, and why, is told in the [frontend README](https://github.com/bcollier/ignatius-hw4-web#readme), [ARCHITECTURE.md](../ARCHITECTURE.md), the later redesign spec [IMPROVEMENTS.md](../IMPROVEMENTS.md), and every prompt in [PROMPT_LOG.md](../../PROMPT_LOG.md).
