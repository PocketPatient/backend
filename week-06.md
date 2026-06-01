# Week 6 — Jun 30–Jul 6 | Phase 1: Content Pipeline (buffer)
## Theme: Polish Phase 0+1, prepare for Phase 2

---

### Dev A (Mahir) — Backend

#### Task 1: API cleanup and validation
- Add comprehensive input validation to all existing endpoints using Pydantic validators
- Standardize error responses: `{"detail": "message", "code": "ERROR_CODE"}`
- Add request/response logging middleware (structured JSON logs)
- Add rate limiting middleware: 100 req/min per user for standard endpoints, 20 req/min for auth endpoints
- Review and fix any N+1 query issues in list endpoints (use `selectinload` for relationships)

#### Task 2: Database seed script
- Create `scripts/seed.py` that:
  - Creates 1 professor user, 5 student users
  - Creates 1 course with a class code
  - Enrolls all students
  - Imports the sample disease document (2 units, 6-8 diseases)
  - Releases Unit 1
- This seed script will be used for all Phase 2 development and testing

#### Task 3: Begin Session and Message models
- Create SQLAlchemy models (don't build endpoints yet — that's Week 7):

**sessions**
| Column | Type | Constraints |
|--------|------|------------|
| id | UUID | PK |
| disease_id | UUID | FK → diseases.id |
| user_id | UUID | FK → users.id |
| course_id | UUID | FK → courses.id |
| started_at | TIMESTAMP | |
| completed_at | TIMESTAMP | nullable |
| status | ENUM('active','diagnosed','abandoned') | default 'active' |
| turn_count | INT | default 0 |
| avg_response_latency_sec | FLOAT | nullable |
| created_at | TIMESTAMP | |

**messages**
| Column | Type | Constraints |
|--------|------|------------|
| id | UUID | PK |
| session_id | UUID | FK → sessions.id |
| role | ENUM('student','patient','system') | NOT NULL |
| content | TEXT | NOT NULL |
| sent_at | TIMESTAMP | NOT NULL |
| delivered_at | TIMESTAMP | nullable |
| read_at | TIMESTAMP | nullable |
| response_latency_sec | FLOAT | nullable |
| is_nudge | BOOLEAN | default false |
| token_count | INT | nullable |
| created_at | TIMESTAMP | |

- Run Alembic migration
- No endpoints yet — just the models

---

### Dev B (Tyler) — Frontend

#### Task 1: UI polish pass on existing screens
- Review all screens for consistency:
  - Consistent padding/margins (16dp standard)
  - Loading states on all API calls (shimmer or spinner)
  - Error states with retry buttons
  - Empty states with helpful messages
- Test on both iOS and Android — fix any platform-specific rendering issues

#### Task 2: Offline handling
- Add `connectivity_plus` package to detect network state
- Show a banner when offline: "You're offline — some features may be unavailable"
- Cache course list locally using `shared_preferences` or `hive` for offline viewing
- API calls should fail gracefully with user-friendly messages when offline

#### Task 3: Begin chat UI scaffolding
- Create `chat_screen.dart` with the basic structure (don't connect to API yet):
  - Message list (scrollable, bottom-aligned like any chat app)
  - Text input field at bottom with send button
  - Placeholder messages to test layout:
    - Patient message (left-aligned, gray bubble)
    - Student message (right-aligned, Rutgers scarlet bubble)
  - Timestamp under each message
  - "Awaiting patient reply..." indicator

---

### Joint
- Run the full app against the seed data — both professor and student flows
- List any bugs or UX issues — fix the critical ones this week
- Phase 1 is complete — prepare for Phase 2 kickoff next week
