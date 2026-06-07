# ShadeMatch iOS App — Implementation Spec (From Scratch)

This document is a step-by-step specification so that **any developer** can implement the ShadeMatch Swift iOS app from scratch without prior knowledge of the web app. Follow sections in order where dependencies are noted.

---

## Table of Contents

1. [Prerequisites and Setup](#1-prerequisites-and-setup)
2. [Project Structure (Files and Folders)](#2-project-structure-files-and-folders)
3. [Configuration and API Base URL](#3-configuration-and-api-base-url)
4. [API Contract (Exact Request/Response Shapes)](#4-api-contract-exact-requestresponse-shapes)
5. [API Client Implementation](#5-api-client-implementation)
6. [Local Storage (UserDefaults/Keychain)](#6-local-storage-userdefaultskeychain)
7. [Color Science (CIE2000 + Mixing)](#7-color-science-cie2000--mixing)
8. [App-Wide Models](#8-app-wide-models)
9. [App Entry and Navigation Flow](#9-app-entry-and-navigation-flow)
10. [Registration Feature](#10-registration-feature)
11. [Login Feature](#11-login-feature)
12. [Ishihara Test Feature](#12-ishihara-test-feature)
13. [Color Mixing Game Feature](#13-color-mixing-game-feature)
14. [Results Feature](#14-results-feature)
15. [Theming and Appearance](#15-theming-and-appearance)
16. [Copy and Localized Strings](#16-copy-and-localized-strings)
17. [Assets (Ishihara Plates)](#17-assets-ishihara-plates)
18. [Testing and Validation Checklist](#18-testing-and-validation-checklist)

---

## 1. Prerequisites and Setup

- **Xcode:** 15.x or later (or latest stable).
- **iOS Deployment Target:** 16.0 or 17.0 (specify in project settings).
- **Language:** Swift 5.
- **UI Framework:** SwiftUI only.
- **Architecture:** MVVM; use `@Observable` (iOS 17+) or `ObservableObject` + `@Published` for ViewModels. All UI updates must occur on the main thread; annotate ViewModels with `@MainActor` where they drive views.
- **Backend:** The existing Flask app must be running and reachable (e.g. `https://your-app.onrender.com` or `http://localhost:5000` for dev). No backend code changes are required.

**Create the project:**

1. File → New → Project → App.
2. Product Name: `ShadeMatch` (or your choice).
3. Interface: SwiftUI. Life Cycle: SwiftUI App. Language: Swift.
4. Uncheck "Include Tests" if you prefer; you can add a test target later for ColorScience.

---

## 2. Project Structure (Files and Folders)

Create the following groups and files. (In Xcode, "groups" are the yellow folders; you can add real folders on disk and add them as "groups" to keep disk and project in sync.)

```
ShadeMatch/
├── App/
│   └── ShadeMatchApp.swift          # @main entry, WindowGroup
├── Features/
│   ├── Registration/
│   │   ├── RegistrationView.swift
│   │   └── RegistrationViewModel.swift
│   ├── Login/
│   │   ├── LoginView.swift
│   │   └── LoginViewModel.swift
│   ├── Ishihara/
│   │   ├── IshiharaTestView.swift
│   │   ├── IshiharaPlateView.swift
│   │   ├── IshiharaResultsView.swift
│   │   └── IshiharaViewModel.swift
│   ├── ColorGame/
│   │   ├── ColorGameView.swift
│   │   ├── PigmentButtonView.swift
│   │   └── ColorGameViewModel.swift
│   ├── Results/
│   │   ├── ResultsView.swift
│   │   └── ResultsViewModel.swift
│   └── Home/
│       └── HomeView.swift           # Landing after login; navigates to Game or Results
├── Services/
│   ├── APIService.swift            # Single class: base URL, POST, decode
│   └── SessionStorage.swift        # UserDefaults/Keychain wrapper for userId
├── Models/
│   ├── API/                        # DTOs for API only
│   │   ├── RegisterDTO.swift
│   │   ├── LoginDTO.swift
│   │   ├── SessionDTO.swift
│   │   └── ResultsDTO.swift
│   ├── RGB.swift                   # (r, g, b) 0-255
│   ├── Pigment.swift               # Enum: white, black, red, yellow, blue + rgb
│   ├── TargetColor.swift           # name + RGB for target pool
│   └── IshiharaPlate.swift         # id, imageName, options, correctAnswer
├── Utilities/
│   └── ColorScience.swift          # LabColor, sRGBToLab, deltaE2000, mixing
├── Resources/
│   ├── IshiharaPlates.json         # Plate metadata (see Section 12)
│   ├── TargetColors.json           # Optional: target color pool; or hardcode in ColorGameViewModel
│   └── Assets.xcassets/             # App icon + Ishihara images (see Section 17)
└── Info.plist
```

You can omit `TargetColors.json` and define the target color pool in code (see Section 13).

---

## 3. Configuration and API Base URL

- Add a **custom key** to `Info.plist`, e.g. `API_BASE_URL`, type String, value e.g. `https://your-app.onrender.com` (no trailing slash). For debug, use a different scheme or a separate key like `API_BASE_URL_DEBUG` and switch in code based on `#if DEBUG`.
- In code, read the base URL once at launch (e.g. in `APIService` or a small `AppConfig` struct) and use it for all requests. If the key is missing, fall back to a default or show an error.

---

## 4. API Contract (Exact Request/Response Shapes)

Backend base URL is `{API_BASE_URL}`. All endpoints are **POST** with `Content-Type: application/json`. Request bodies are JSON objects. The backend returns JSON and uses **snake_case** keys; the iOS app should encode requests in **snake_case** for session/results endpoints (to match Flask) or use a custom encoder; for register/login the web sends camelCase `userId` — see below.

### 4.1 POST `/register`

**Request body (JSON):**

- `birthdate`: string, format `YYYY-MM-DD` (e.g. `"1990-05-15"`).
- `gender`: string, one of `"male"`, `"female"`.

**Success response (HTTP 200):**

```json
{
  "status": "success",
  "userId": "ABC12X"
}
```

`userId` is a 6-character string (uppercase letters and digits). Backend generates it.

**Error response (HTTP 400):**

```json
{
  "status": "error",
  "message": "You must be born before 2015 to participate."
}
```

**iOS:** Send body with keys `birthdate` and `gender`. Decode response with key `userId` (camelCase) or use a decoder that accepts both.

---

### 4.2 POST `/login`

**Request body (JSON):**

- `userId`: string, 6 characters (the backend expects this key as **camelCase** `userId`).

**Success response (HTTP 200):**

```json
{
  "status": "success",
  "birthdate": "1990-05-15",
  "gender": "male"
}
```

**Error response (HTTP 404):**

```json
{
  "status": "error",
  "message": "Invalid user ID"
}
```

**iOS:** Send body with key `userId`. On 200, store `userId` locally and proceed to the main app. On 404, show “Invalid user ID”.

---

### 4.3 POST `/save_session`

**Request body (JSON, all keys snake_case):**

| Key          | Type    | Description                          |
|-------------|---------|--------------------------------------|
| `user_id`   | string  | 6-char user ID                       |
| `target_r`  | int     | 0–255                                |
| `target_g`  | int     | 0–255                                |
| `target_b`  | int     | 0–255                                |
| `drop_white`| int     | ≥ 0                                  |
| `drop_black`| int     | ≥ 0                                  |
| `drop_red`  | int     | ≥ 0                                  |
| `drop_yellow`| int    | ≥ 0                                  |
| `drop_blue` | int     | ≥ 0                                  |
| `delta_e`   | double  | CIE2000 from client                  |
| `time_sec`  | double  | Elapsed seconds                      |
| `timestamp` | string  | ISO 8601 (e.g. `"2024-01-15T14:30:00Z"`) |
| `skipped`   | bool    | `false` for save_session             |

**Success response (HTTP 200):**

```json
{ "status": "success" }
```

**Error response (HTTP 500):**

```json
{ "status": "error", "error": "..." }
```

---

### 4.4 POST `/save_skip`

Same request body as `/save_session`, but set `skipped` to `true`. `delta_e` can be `null` or omitted if the user skipped without submitting. Same success/error response shape.

---

### 4.5 POST `/get_user_results`

**Request body (JSON):**

- `user_id`: string, 6-char user ID.

**Success response (HTTP 200):**

```json
{
  "status": "success",
  "results": [
    {
      "id": 1,
      "target_color": "RGB(255, 102, 30)",
      "drops": {
        "white": 2,
        "black": 0,
        "red": 1,
        "yellow": 1,
        "blue": 0
      },
      "delta_e": 3.45,
      "time_sec": 120.5,
      "timestamp": "2024-01-15T14:30:00",
      "skipped": false
    }
  ],
  "total_sessions": 1
}
```

`delta_e` may be a number or the string `"N/A"` when skipped. `timestamp` is ISO 8601 or null.

**Error (HTTP 400):** `{ "status": "error", "message": "User ID is required" }`  
**Error (HTTP 500):** `{ "status": "error", "message": "..." }`

---

## 5. API Client Implementation

- **Single service:** e.g. `APIService` (singleton or injected). It holds the base URL and performs all HTTP calls.
- **Method:** `func post<T: Decodable>(path: String, body: Encodable) async throws -> T`. Build URL as `baseURL + path` (e.g. path `"/register"`). Set `httpMethod = "POST"`, set header `Content-Type: application/json`, set `httpBody` from `JSONEncoder().encode(body)`.
- **Encoding:** For `/register` and `/login`, use a struct with camelCase property names and encode with default encoder (or one that outputs camelCase if your DTOs are camelCase). For `/save_session`, `/save_skip`, and `/get_user_results`, the backend expects **snake_case**; use `JSONEncoder()` with `keyEncodingStrategy = .convertToSnakeCase` so Swift `camelCase` properties become `snake_case` in JSON.
- **Decoding:** Use `JSONDecoder()` with `keyDecodingStrategy = .convertFromSnakeCase` so `user_id` in JSON maps to `userId` in Swift, etc. Handle `userId` in register response: backend sends `userId` (camelCase); with convertFromSnakeCase it still decodes as `userId` if the key is `userId`.
- **Errors:** On non-2xx status, decode error body if present (e.g. `{ "status": "error", "message": "..." }`) and throw an app-defined error type (e.g. `APIError.serverMessage(String)`). On network failure, throw a generic network error. Callers use `do/catch` or `Result` and update UI (e.g. show alert or inline message).

---

## 6. Local Storage (UserDefaults/Keychain)

- **Stored values:**
  - `userId`: String (6-char). Required for authenticated flows.
  - Optionally: `userBirthdate`, `userGender` (for display only; not required for API).
- **Where:** UserDefaults is sufficient. Key names: e.g. `"shadematch.userId"`, `"shadematch.userBirthdate"`, `"shadematch.userGender"`.
- **Wrapper:** Implement a small `SessionStorage` (or `UserSession`) type that:
  - Reads/writes `userId` (and optional birthdate/gender).
  - Provides a clear method (for “Switch user”) that removes these keys.
- **Usage:** After successful register or login, write `userId` (and optionally birthdate/gender). Before any API call that needs `user_id`, read `userId`; if nil, show login/registration.

---

## 7. Color Science (CIE2000 + Mixing)

**File:** `Utilities/ColorScience.swift`

**Requirements:**

1. **sRGB → Lab:** Convert sRGB (each channel 0–255, then normalized to 0–1) to CIE XYZ (D65, 2°), then to CIE Lab. Use the same formulas as colormath so that Delta E matches the backend’s `/calculate` and stored values.
2. **CIE2000 Delta E:** Given two Lab values, compute ΔE₀₀ (Kl=Kc=Kh=1). Algorithm: CIE 142-2001 (or equivalent implementation that matches colormath).
3. **Mixing (for the game):** Given drop counts for five pigments (white, black, red, yellow, blue), compute a single RGB by **weighted average**: for each pigment, weight = count / totalDrops; mixed R = Σ(r_pigment * weight), same for G and B. If totalDrops == 0, use white (255, 255, 255). Round mixed components to integers and clamp to 0–255.

**Public API to implement:**

- `struct LabColor { let L, a, b: Double }`
- `static func sRGBToLab(r: Double, g: Double, b: Double) -> LabColor`
- `static func deltaE2000(color1: LabColor, color2: LabColor) -> Double`
- `static func deltaE2000(rgb1: (r: Double, g: Double, b: Double), rgb2: (r: Double, g: Double, b: Double)) -> Double` (convenience: convert both to Lab, then call deltaE2000).
- Mixing is not inside ColorScience; it lives in the game ViewModel using pigment RGBs and drop counts (see Section 13). You can add a helper `static func mixedRGB(drops: [Pigment: Int]) -> (r: Int, g: Int, b: Int)` in ColorScience or in the ViewModel.

**Validation:** Unit test: for two known RGB pairs, compare Swift `deltaE2000` output with the backend’s `/calculate` response (or with colormath in Python) to ensure numerical parity.

---

## 8. App-Wide Models

- **RGB:** Struct with `r, g, b: Int` (0–255). Add a SwiftUI `Color` extension or computed property for display (e.g. `Color(red: r/255, green: g/255, blue: b/255)`).
- **Pigment:** Enum with cases `white`, `black`, `red`, `yellow`, `blue`. Each case has an associated RGB (e.g. white 255,255,255; black 0,0,0; red 255,0,0; yellow 255,255,0; blue 0,0,255). Conform to `Identifiable`/`Hashable` for SwiftUI and dictionaries.
- **TargetColor:** Struct with `name: String` and `rgb: RGB` (or `r, g, b`). Used for the target color pool in the game.
- **IshiharaPlate:** Struct with `id: Int`, `imageName: String` (asset name), `options: [String]`, `correctAnswer: String`. Load from `IshiharaPlates.json` (see Section 12).

---

## 9. App Entry and Navigation Flow

**Flow summary:**

1. **Launch** → If `userId` is stored, show **Home** (or directly **ColorGame** with a “Results” button). If not, show **Registration or Login** (e.g. a root view with tabs or buttons “Register” / “Log in”).
2. **After Register** → Backend returns `userId`; store it. Then either show **Ishihara Test** (to mirror web) or show **Home**. Spec: after registration, go to **Ishihara Test**; after test pass, show “Continue to Color Mixing” and navigate to **Home** (or ColorGame). After test fail, show “Access Denied” and do not allow access to the game until user logs in again (optional: allow retry of test).
3. **After Login** → Store `userId`, go to **Home**.
4. **Home** → Shows “Start Color Vision Test” (or “Color Mixing”) and “View Results”, and “Switch User”. Tapping “Color Mixing” opens **ColorGame**. Tapping “View Results” opens **Results**.
5. **ColorGame** → User plays; on “Switch User”, clear stored userId and go back to Login/Registration.
6. **Results** → Fetched from API using stored `userId`; show list. Back button to Home.

**Navigation implementation:** Use SwiftUI `NavigationStack` (iOS 16+) with `NavigationPath` or a simple enum state (e.g. `enum AppScreen { case auth, ishihara, home, colorGame, results }`) and one root view that switches on that state. Alternatively, use a tab-based or list-based home that pushes ColorGame and Results.

---

## 10. Registration Feature

**Views:**

- **RegistrationView:** Form with:
  - Date picker for birthdate (max date: Dec 31, 2014 — “must be born before 2015”).
  - Picker or segmented control for gender: Female / Male.
  - Primary button: “Start Color Vision Test” (or “Register” then navigate).
  - Secondary link/button: “Already have an ID? Log in” → navigate to Login.
- **Validation:** Before submitting, check birth year < 2015; if not, show inline error: “You must be born before 2015 to participate.” Do not call API.
- **Submission:** Call `APIService.post("/register", body: RegisterRequest(birthdate: "YYYY-MM-DD", gender: "male"|"female"))`. On success, decode `RegisterResponse(userId: String)`; store `userId` (and optionally birthdate, gender) via `SessionStorage`; then navigate to **Ishihara Test** (or to a “Show your ID” screen that displays `userId` and a “Continue” button that then goes to Ishihara). On 400, decode error and show `message` in the UI. On network error, show generic “Connection error. Please try again.”

**ViewModel:** `RegistrationViewModel`: `@Published` birthdate, gender, errorMessage, isLoading. Method `register()` async; sets loading, calls API, on success saves and sets navigation state; on failure sets errorMessage.

---

## 11. Login Feature

**Views:**

- **LoginView:** Text field for 6-character user ID (uppercase letters and digits). Validation: exactly 6 characters. Button “Log in”. Link “New user? Register” → navigate to Registration.
- **Submission:** POST `/login` with body `{ "userId": "<entered>" }`. On 200: store returned userId (and optionally birthdate, gender); navigate to Home. On 404: show “Invalid user ID”. On 500 or network error: show “Something went wrong. Please try again.”

**ViewModel:** `LoginViewModel`: `@Published` userId (text binding), errorMessage, isLoading. Method `login()` async; same pattern as registration.

---

## 12. Ishihara Test Feature

**Data:**

- **Source:** Use the same plate set as the web app. Web uses `static/ishihara_test_data.csv` with columns: `no`, `file_source`, `option1`..`option5`, `solution`. For parity, use **first 5 plates** only (or match the web’s current subset).
- **Bundle:** Add a JSON file `IshiharaPlates.json` in the app bundle. Format:

```json
[
  {
    "id": 1,
    "imageName": "96",
    "options": ["9", "lines", "6", "96", "9B"],
    "correctAnswer": "96"
  },
  ...
]
```

`imageName` should match the asset name in Assets.xcassets (e.g. you’ll add an image set named `96` whose source is `ishihara/96.png`). For “vonalak01” etc., use that as the asset name. Omit file extension in JSON.

**Assets:** Add each plate PNG to Assets.xcassets (see Section 17). Name each image set exactly as in `imageName` (e.g. `96`, `05`, `74`, `vonalak01`, `16` for the first 5 plates).

**Flow:**

1. **IshiharaTestView:** Load plates from JSON. Show progress (e.g. “Plate 1 of 5”). Show current plate image (circular mask), question text (“What do you see?”), and a grid of option buttons (from `options`). Buttons: “Previous”, “Next” (or “Finish” on last plate). User selects one option per plate; selection is highlighted.
2. **Scoring:** When user taps “Finish” (or “Next” on last plate), compute: correctCount = number of plates where `userAnswers[index] == plate.correctAnswer`. score = (correctCount / totalPlates) * 100 (integer).
3. **Pass/Fail:** Threshold 90%. If score >= 90: show **IshiharaResultsView** with “Test Passed”, score, and button “Continue to Color Mixing App”; on tap, navigate to Home (or ColorGame). If score < 90: show “Test Failed — Access Denied” and message that only users with normal color vision (90%+) can participate; do not navigate to game (optionally show “Try again” to re-run the test, or “Log in” if they had an ID).
4. **Registration after pass:** The web app registers the user *after* they pass the Ishihara test (with birthdate/gender from earlier). In the iOS flow, you already registered before Ishihara and got `userId`; so after Ishihara pass, you already have `userId`. Just navigate to Home. If you prefer to register only after pass: collect birthdate/gender on a screen before Ishihara, and call `/register` only when they pass; then show and store `userId` and go to Home.

**ViewModel:** `IshiharaViewModel`: Load plates from bundle; `@Published` currentPlateIndex, userAnswers: [String?], showResults, passed, score. Methods: `selectAnswer(option)`, `next()`, `previous()`, `finish()` (compute score, set passed, showResults).

---

## 13. Color Mixing Game Feature

**Target color pool:**

- The web uses a fixed set of 40 target colors (11 basic + 29 skin) and builds a session pool of 11 colors: first 3 fixed (Orange, Purple, Green) + 3 weighted from remaining basic + 5 weighted from skin. For a minimal first version, use a **flat list** of the same 40 colors and pick a random one per round (or implement the same weighted selection as in `main.js` for full parity). Define the 40 colors in code (see `static/main.js` lines 333–378) or in a JSON file. Each entry: name, r, g, b (0–255).

**Pigments and mixing:**

- Pigments: white (255,255,255), black (0,0,0), red (255,0,0), yellow (255,255,0), blue (0,0,255).
- **Mixed color:** totalDrops = sum of drop counts. If totalDrops == 0, mixed = (255,255,255). Else: for each pigment, weight = count / totalDrops; mixedR = Σ(r_pigment * weight), same for G, B. Round and clamp to 0–255. This is simple weighted average (no Mixbox).

**Delta E:**

- After every drop change, compute mixed RGB as above, then `ColorScience.deltaE2000(rgb1: targetRGB, rgb2: mixedRGB)`. Keep this value in memory for API payloads and the ≤0.01 auto-save threshold. **Do not show ΔE in the participant UI** (web parity: researchers still receive `delta_e` in the DB).

**UI layout:**

- Top: Two large rectangles side by side — left: **target color**, right: **current mixed color**. Below: “RGB: [r, g, b]” for the mixed color (no ΔE label).
- Middle: **Five pigment controls.** Each: a circular color swatch (the pigment color), a label showing the drop count (e.g. “0”), a “+” (or tap circle to add) and “−” button. Tapping + increments that pigment’s count and updates mixed color and Delta E; tapping − decrements (minimum 0).
- Bottom: Buttons: **Start**, **Stop**, **Skip**, **Restart**, **Retry**, **Switch User**. Timer display: “Time: &lt;seconds&gt;.&lt;tenths&gt; s”.
- **Start:** Pick a random target from the session pool (or the full 40); set it as current target; reset drop counts to 0, mixed to white; start timer (e.g. 0.0 s, increment every 0.1 s); enable pigment controls and Stop/Skip.
- **Stop:** Compute current deltaE (target vs mixed); POST `/save_session` with user_id, target_r/g/b, drop_white/black/red/yellow/blue, delta_e, time_sec, timestamp (ISO), skipped=false. On success: stop timer, disable controls, show “Saved” or move to next round (pick new target, reset mix and timer). On failure: show error, optionally retry.
- **Skip:** When skipping **while still mixing** and current deltaE &gt; 0.01 (and not immediately after **Stop**, which already saved): show a **blocking modal** (facial-prosthesis context) with three choices — identical / acceptable / unacceptable — map to JSON `skip_perception`: `identical` | `acceptable` | `unacceptable`. POST `/save_skip` with the same numeric fields as today **plus** `skip_perception`; **only after success**, advance to the next target (or finish). If ΔE ≤ 0.01 (“Next color”), or after Stop, or no valid ΔE: advance without `save_skip` or modal, matching web `main.js`.
- **Restart:** Same target again; reset drops to 0 and timer to 0; keep controls enabled.
- **Retry:** New random target; reset drops and timer.
- **Switch User:** Optionally if there is in-progress data (drops &gt; 0 or timer &gt; 0), POST current session (save_session or save_skip) for current userId; then clear stored userId and navigate to Login/Registration.

**ViewModel:** `ColorGameViewModel`: `@MainActor`, `@Published` targetRGB, dropCounts [Pigment: Int], mixedRGB, deltaE, timerSeconds (Double), isPlaying, currentSessionSaved, errorMessage. Methods: `start()`, `stop()`, `skip()`, `restart()`, `retry()`, `addDrop(Pigment)`, `removeDrop(Pigment)`, `switchUser()`. On add/remove, recompute mixedRGB and deltaE. Use a Timer or Combine to update timerSeconds every 0.1 s while isPlaying. When calling API, use stored userId from SessionStorage; encode session payload with snake_case (see Section 4.3).

---

## 14. Results Feature

**View:** **ResultsView:** On appear, call `POST /get_user_results` with body `{ "user_id": "<stored userId>" }`. Show loading indicator. On success: display a list of rows — each row shows target_color (or “RGB(r,g,b)”), drops (white/black/red/yellow/blue), time_sec, timestamp, skipped status, and **skip_perception** (for skipped rows with a rating; otherwise em dash). **Do not show ΔE** in the results UI (values may still be present in JSON for research tools). Sort by timestamp descending (backend already returns in that order). On error: show “Could not load results” and retry button.

**ViewModel:** `ResultsViewModel`: `@Published` results: [ResultRow], isLoading, errorMessage. Method `loadResults()` async; reads userId from SessionStorage, calls API, decodes response, maps to a simple `ResultRow` model (id, targetColor string, drops, timeSec, timestamp, skipped, skipPerception optional).

---

## 15. Theming and Appearance

- **Background:** #2e2e2e (dark gray), same as web `--bg-color`.
- **Surface/cards:** #424242 (`--surface-color`).
- **Accent/buttons:** #565656 (`--accent-color`).
- **Text:** White (#ffffff) on dark backgrounds.
- **Borders:** #565656 where needed.
- Apply these in SwiftUI: define `Color(hex: "2e2e2e")` etc. (implement a small hex initializer if needed), and set as background for main views and for cards/buttons. Use `.foregroundColor(.white)` for labels on dark areas.

---

## 16. Copy and Localized Strings

Use these strings (or equivalent) for consistency with the web app:

- “Welcome!” / “Please enter your birthdate and gender to get started.”
- “Birthdate:” / “Gender:” / “Female” / “Male”
- “Start Color Vision Test” / “Already have an ID? Log in”
- “Log in” / “Your 6-character ID:” / “New user? Register”
- “Invalid user ID”
- “You must be born before 2015 to participate.”
- “Ishihara Color Vision Test” / “Identify the number or pattern you see”
- “What do you see in this plate?” / “Previous” / “Next” / “Finish Test”
- “Test Complete!” / “Test Passed!” / “Continue to Color Mixing App”
- “Test Failed — Access Denied” / “You do not have permission to access the color matching test.”
- “Welcome to your digital color mixing palette!” / “Once you are ready, hit Start and enjoy the game!”
- “RGB:” (ΔE not shown to participants)
- “Start” / “Stop” / “Skip” / “Restart” / “Retry” / “Switch User”
- “Time:” (with “s” or “sec”)
- “View Results” / “Your Color Matching Results”
- “S.H.A.D.E. — Study of Human Accuracy in Digital Experiments”

You can put these in a `Strings.swift` or in Localizable.strings later.

---

## 17. Assets (Ishihara Plates)

- **Source:** Copy PNG files from the web app’s `ishihara/` folder (e.g. `96.png`, `05.png`, `74.png`, `vonalak01.png`, `16.png` for the first 5 plates). Fix any typo in filenames (e.g. `ishihara.csv` had `74.jpb` / `vonalak03` / `02.jps`; use the actual filenames that exist in `ishihara/`).
- **Xcode:** In Assets.xcassets, create an **Image Set** for each plate. Name it exactly as you use in `IshiharaPlates.json` (e.g. `96`, `05`, `74`, `vonalak01`, `16`). Drag the corresponding PNG into the 1x slot. Use “Single Size” if you only have one resolution.
- **App Icon:** Add an app icon set in Assets and assign it in the target’s General tab.

---

## 18. Testing and Validation Checklist

- **API:** With backend running, test register (valid + invalid birth year), login (valid + invalid ID), save_session and save_skip (check DB or web results page), get_user_results. Verify JSON keys (snake_case vs camelCase) and decoding.
- **ColorScience:** Unit test: e.g. two RGB pairs; compare Swift `deltaE2000` result with Python `colormath` or with `/calculate` response. Ensure sRGB→Lab matches (same ref white, same matrix).
- **Mixing:** With known drop counts, compute mixed RGB by hand (weighted average) and compare with app display.
- **Ishihara:** Load 5 plates; complete test with all correct → 100%, pass. One wrong → 80%, fail. Check “Continue” only when passed.
- **Navigation:** Register → Ishihara → pass → Home → Color Game; Login → Home → Results; Switch User → back to Login; no game access without userId.
- **Offline:** With network off, register/login/save/results should show a clear error (no silent failure).

---

## Summary: Implementation Order

1. Create Xcode project and folder structure (Section 2).
2. Add Info.plist and API base URL (Section 3).
3. Implement DTOs and API contract (Section 4), then APIService (Section 5).
4. Implement SessionStorage (Section 6).
5. Implement ColorScience (Section 7) and app models RGB, Pigment, TargetColor (Section 8).
6. Implement app entry and navigation (Section 9).
7. Implement Registration (Section 10) and Login (Section 11).
8. Add Ishihara data and assets (Sections 12, 17); implement Ishihara views and ViewModel.
9. Implement ColorGame ViewModel and views (Section 13); wire Start/Stop/Skip/Restart/Retry and API.
10. Implement Results (Section 14).
11. Apply theming and strings (Sections 15, 16).
12. Run through testing checklist (Section 18).

End of spec.
