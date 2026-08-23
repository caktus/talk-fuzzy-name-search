import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium", layout_file="layouts/slides.slides.json")

with app.setup(hide_code=True):
    import os
    import sys
    from pathlib import Path

    import marimo as mo
    import polars as pl
    import sqlalchemy

    engine = sqlalchemy.create_engine(os.environ.get("DATABASE_URL"))

    django_project_dir = Path(__file__).parent.joinpath("../../..").resolve()

    sys.path.insert(0, str(django_project_dir))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fuzzy_demo.settings")
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

    visuals = Path(__file__).resolve().parent / "visuals"

    import django  # noqa

    django.setup()

    from django.db.models import TextField
    from django.db.models.functions import Cast

    from records.models import CourtRecord


@app.cell
def _():
    """Good afternoon, everyone. Welcome to our talk."""
    # The first slide is a title slide, so we don't need to use mo.vstack() or mo.hstack() here.
    mo.vstack(
        [
            mo.md("""
    ## Search-as-you-type for 54 Million Names

    **PostgreSQL + Django for Fuzzy Name Matching at Scale**

    Caktus Group // DjangoCon US 2026
    """),
        mo.hstack([mo.md("### https://cakt.us/fuzzy-repo"),
                   mo.image((visuals / "qr_repo.png").absolute(), width="25%")])
        ])
    return


@app.cell
def _():
    """
    Introductions...
    """
    mo.vstack(
        [
            mo.md("# Speakers"),
            mo.hstack(
                [
                    mo.md("""
            ## Tobias McNulty
            ### Chief Executive Officer + Co-founder
            """),
                    mo.md("""
            ## Gerald Carlton
            ### Scrum Master & Quality Assurance Analyst
            """),
                ],
                widths="equal",
                gap=3,
            ),
        ]
    )
    return


@app.cell
def _():
    """
    Introductions...
    """
    mo.vstack(
        [
            mo.md("# Contributors"),
            mo.hstack(
                [
                    mo.md("""
            ## Colin Copeland
            ### Chief Technical Officer + Co-founder
            """),
                    mo.md("""
            ## Simon Kagwi
            ### Developer
            """),
                ],
                widths="equal",
                gap=3,
            ),
        ]
    )
    return


@app.cell
def _():
    """
    To set the stage today, we want to talk about criminal record expungement.
    This is an area of legal aid where non-profit organizations, public defenders,
    and sometimes private attorneys help community members clear eligible charges
    from their records, such as dismissed or not guilty charges.

    For someone trying to secure housing or apply for a job, an expungement can
    be life-changing. But under the hood, the process of preparing these petitions
    is painfully manual. Attorneys and paralegals must manually track down potentially
    hundreds of records of a period of decades, verify every single charge, and
    transcribe everything onto strictly formatted court forms.
    """

    def flowchart(highlight=None):
        # Highlights one node per sub-slide so the deck can walk through the process step by step.
        style_line = f"style {highlight} stroke:#F54927,stroke-width:4px" if highlight else ""
        return f"""
        %%{{init: {{"themeVariables": {{"fontSize": "28px"}}}}}}%%
        flowchart LR
            A["📋<br/>Client intake<br/>and data collection"]
            A --> B["🔍<br/>Record Search"]
            B --> C["⚖️<br/>Statute and<br/>eligibility rules"]
            C --> D["📄<br/>Form generation<br/>(strict court formats)"]
            D --> E["✅<br/>Expungement<br/>petition filed"]
            {style_line}
        """

    # Overrides the deck's top-aligned layout to render this as a centered title slide.
    mo.md("""
        # Criminal record expungement

        ### The process by which a criminal case is permanently removed from state record
        """)
    return (flowchart,)


@app.cell
def _(flowchart):
    """
    Everything starts with intake. A client comes in, and staff need to
    collect every name they've gone by, their date of birth, and every
    county they've lived in, so nothing gets missed in the search that
    follows.
    """
    mo.vstack(
        [
            mo.md("""
        ### 1. Client intake and data collection
        """),
            mo.mermaid(flowchart("A")),
            mo.md("""
        Attorneys and paralegals gather a client's history: names, aliases,
        dates of birth, and every county where they may have a record.
        """),
        ]
    )
    return


@app.cell
def _(flowchart):
    """
    This is the step we're focused on today. Staff have to search court and
    law-enforcement databases for every record tied to that client, and
    names are typed inconsistently — misspellings, nicknames, abbreviations —
    so a simple exact-match search misses records.
    """
    mo.vstack(
        [
            mo.md("""
        ### 2. Record search
        """),
            mo.mermaid(flowchart("B")),
            mo.md("""
        Staff search court and law-enforcement databases for every matching
        record — and this is where the process bogs down, since names are
        typed, misspelled, and abbreviated inconsistently across systems.
        """),
        ]
    )
    return


@app.cell
def _(flowchart):
    """
    Once every record is found, each charge gets checked against state
    statutes to see if it even qualifies for expungement, and how long the
    client has to wait before they can petition for it.
    """
    mo.vstack(
        [
            mo.md("""
        ### 3. Statute and eligibility rules
        """),
            mo.mermaid(flowchart("C")),
            mo.md("""
        Each charge found is checked against state statutes to determine
        whether it's eligible for expungement, and under what waiting period.
        """),
        ]
    )
    return


@app.cell
def _(flowchart):
    """
    From there, every eligible charge has to be transcribed onto strictly
    formatted court petition forms, by hand, one at a time — another
    manual, error-prone step in this process.
    """
    mo.vstack(
        [
            mo.md("""
        ### 4. Form generation
        """),
            mo.mermaid(flowchart("D")),
            mo.md("""
        Eligible charges are transcribed onto strictly formatted court
        petition forms — by hand, one charge at a time.
        """),
        ]
    )
    return


@app.cell
def _(flowchart):
    """
    Finally, the completed petition is filed with the court. If it's
    granted, the record is cleared — but getting here can take weeks of
    manual work for a single client.
    """
    mo.vstack(
        [
            mo.md("""
        ### 5. Expungement petition filed
        """),
            mo.mermaid(flowchart("E")),
            mo.md("""
        The completed petition is filed with the court, and if granted, the
        record is cleared.
        """),
        ]
    )
    return


@app.cell
def _():
    """
    When we first took on this project, our assumption was that the core
    challenge would be PDF form generation or other aspects of the
    workflow—taking client data, applying NC statute rules, and cleanly
    injecting it into standardized court paperwork. But then we held a
    dedicated discovery workshop with our client.

    During the workshop, we realized that finding the proper records
    quickly was the true bottleneck preventing eligible community
    members from getting help.

    This step is particularly critical because expungement is often
    a one-shot process. Depending on the type of expungement, if an
    attorney fails to include a charge on the initial petition and it
    gets submitted incompletely, that client may be legally barred from
    ever petitioning for that expungement again. In other words, you don't
    get a do-over. Attorneys must locate every single record across district
    and superior courts in the entire state before filing. Finding the complete
    set of records is a strict legal requirement where missing a single file
    has permanent consequences for the client.
    """
    # Raw HTML (not mo.video) so the <video> carries data-autoplay —
    # reveal.js only auto-plays media with data-autoplay when the slide
    # activates, and pauses it when you navigate away. The data URI is
    # built at cell-run time, so the .py stays small.

    mo.vstack(
        [
            mo.md("""
        ### What we learned at the workshop
        """),
            mo.image((visuals / "workshop.jpg").absolute(), width="100%"),
        ]
    )
    return


@app.cell
def _():
    """
    Here is the core problem: the state transitioned away from its legacy system
    to a "modern" web portal. While you might expect a newer system to speed up
    legal workflows, in reality, it severely restricted how attorneys can search
    for records.

    As you can see on screen, doing a simple name search in this portal routinely
    takes 10 to 15 seconds—or longer—per query. In our discovery workshop with the
    client, staff shared that under the new portal system, their record-pulling time
    grew from 3 hours up to 10 hours per client. To make matters worse, while the
    system claims to offer fuzzy search matching, the underlying logic is poorly
    implemented, ballooning an 6 item search result to 200+ results when selected.
    When enabled, it ends up matching too many records, ballooning search results
    with irrelevant records rather than giving attorneys what they actually need.
    And because the new system caps the search results at 200 records, which can
    mostly be false positives, the risk of missing records is real.
    """
    mo.video(
        src=str(visuals / "02-CourtsPortalRecording.mp4"),
        controls=True,
        muted=True,
        autoplay=True,
        loop=False,
        width="100%",
    )
    return


@app.cell
def _():
    """
    Here's the concrete cost of that "fuzzy" problem. The portal has a
    "sounds like" toggle. Turn it off and a name search returns a small,
    correct set of records. Turn it on and that same search balloons to 200+
    hits that are mostly false positives — and the portal hard-caps the list
    at 200 records. So instead of helping, "sounds like" drowns attorneys in
    irrelevant records and risks hiding the ones they actually need. This
    screenshot makes that tradeoff tangible: our tool keeps recall high without
    the noise.
    """
    # When the screenshot is ready, save it as visuals/portal-soundslike.png and
    # swap the placeholder below for the mo.image call (uncomment this line):
    # mo.image((visuals / "portal-soundslike.png").absolute(), width="100%")
    mo.vstack(
        [
            mo.md("""
        # The portal's "sounds like" search
        """),
            mo.image((visuals / "sounds_like.png").absolute(), width="100%")
        ]
    )
    return


@app.cell
def _():
    """
    So why is finding everyone so hard? In our case, we were dealing with a
    database of over 50 million North Carolina court records, and searching by
    name in a dataset that size is not trivial.

    First, we're not just looking for a single record. We're looking for every
    record that belongs to a single person.

    Second, if you think about the lifespan of a criminal record, one can imagine how
    typos are frequent. A name might be typed on the side of the road by an
    arresting officer late at night, transcribed by a busy court clerk at a data
    entry terminal, or supplied by a client who spells their name differently
    across various legal documents, uses nicknames, or hyphenated names.

    On top of that, name collisions are everywhere—thousands of people share the
    exact same first and last name. And date of birth—the one field that could
    easily disambiguate two people—is sometimes missing in court data.

    We realized that if we wanted to build a tool that truly served legal aid
    organizations, public defenders, and community advocates, we had to solve
    the multi-million record fuzzy search problem using our tools of choice,
    Django and PostgreSQL.
    """
    mo.mermaid(
        """
        flowchart LR
            P(("👤<br/>One person"))

            subgraph NA["Record variations — same person"]
                A["Matthew Wilson"]
                A1["Matt Wilson"]
                A2["Mathew Wilson"]
                A3["Matthew J. Wilson"]
                A --> A1
                A --> A2
                A --> A3
            end

            B["Matthew Wilson<br/>(different person, same name)"]
            DOB["📅 Date of birth<br/>often missing from court data"]

            P --> A
            P -. name collision .-> B
            B -. "disambiguated by DOB,<br/>or by reviewing records with the client" .-> DOB

            classDef warning fill:#fef3c7,stroke:#d97706,stroke-width:2px,color:#78350f
            class DOB warning
            class B fill:#fef3c7,stroke:#d97706,color:#78350f
        """
    )
    return


@app.cell
def _():
    mo.md("""
    ## Assumptions About Names

    In 2010, Patrick McKenzie published **[Falsehoods Programmers Believe About Names](https://www.kalzumeus.com/2010/06/17/falsehoods-programmers-believe-about-names/)** — 40 incorrect assumptions engineers routinely make when designing systems.

    Modern legal databases — like the state's new court portal — were built on many of these exact falsehoods.

    **Five of them directly break criminal record searches.**
    """)
    return


@app.cell
def _():
    mo.md("""
    ## Falsehood #1 of 5

    ### 💻 The assumption
    > “People have exactly one canonical full name.”

    ### 🏛️ The reality
    A single client may appear in court records under multiple aliases, hyphenated variations, or maiden names across different counties.
    """)
    return


@app.cell
def _():
    mo.md("""
    ## Falsehood #2 of 5

    ### 💻 The assumption
    > “People's names do not change.”

    ### 🏛️ The reality
    Marriage, divorce, or informal name shifts happen over a lifetime, splitting a person's criminal history across different records.
    """)
    return


@app.cell
def _():
    mo.md("""
    ## Falsehood #3 of 5

    ### 💻 The assumption
    > “People's first names and last names are, by necessity, different.”

    ### 🏛️ The reality
    Naming structures vary across cultures, but traditional systems force every human into a strict 'First Name / Last Name' box.
    """)
    return


@app.cell
def _():
    mo.md("""
    ## Falsehood #4 of 5

    ### 💻 The assumption
    > “People's names are written in ASCII or a single character set.”

    ### 🏛️ The reality
    Special characters, accents, and apostrophes frequently get stripped or corrupted during data entry, leaving unsearchable records.
    """)
    return


@app.cell
def _():
    mo.md("""
    ## Falsehood #5 of 5

    ### 💻 The assumption
    > “Two different data entry operators, given a person's name, will enter bitwise equivalent strings.”

    ### 🏛️ The reality
    Between an arresting officer on a roadside laptop and a rushed court clerk, the same person's name gets typed differently almost every time.
    """)
    return


@app.cell
def _():
    """
    Why is the demo data simulated?

    We can't show real people's names in a public talk -- that's private,
    sensitive information. So the demo runs against a SIMULATED 54-million-
    record dataset instead. A deterministic generator builds it: same seed,
    same data, exactly reproducible. It mirrors the shape of the real data --
    a heavy tail of name frequencies, and the same person appearing across
    several slightly-different records (typos, nicknames, aliases).
    """
    mo.vstack(
        [
            mo.md("# Generating fake data"),
            mo.md("""
    This demo runs on a **simulated 54M-record dataset**:

    ```bash
    DEBUG=False time uv run python manage.py seed_data --count 54000000 ...
    ```
    """),
        ]
    )
    return


@app.cell
def _():
    # Exploring our Demo DB
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    ```python
    class CourtRecord(models.Model):
        "A single raw court record (one capture of a person, not a unified person)."

        first_name = models.CharField(max_length=50)
        last_name = models.CharField(max_length=50)
        middle_name = models.CharField(max_length=50, blank=True)
        date_of_birth = models.DateField()
        # Often one doesn't have a canonical person_id to
        # link rows that are actually the same person, but we
        # included one for demo purposes.
        person_id = UUIDField(
            default=uuid4,
            editable=False,
        )
    ```
    """)
    return


@app.function
def qs_to_df(qs, cols=None):
    """
    Converts a Django queryset to a Polars DataFrame,
    casting the person_id to a string so it can be sorted.
    """
    if cols is None:
        cols = [
            "id",
            "first_name",
            "last_name",
            "date_of_birth",
            "person_id",
        ]
    if "person_id" in cols:
        qs = qs.annotate(person_id_str=Cast("person_id", output_field=TextField()))
        cols[cols.index("person_id")] = "person_id_str"
    return pl.DataFrame(list(qs.values(*cols)))


@app.cell
def _():
    qs_to_df(
        CourtRecord.objects.filter(
            first_name__iexact="sonya",
            last_name__iexact="resyes",
        )[:1000]
    )
    # All persons named "Sonya Resyes" (14 rows)
    return


@app.cell
def _():
    qs_to_df(
        CourtRecord.objects.filter(
            first_name__icontains="Sonya",
            last_name__icontains="Resyes",
            date_of_birth="1961-05-24",
        )[:100]
    )
    # Fourteen (14) records for Sonya Resyes born May 24, 1961 (one person)
    return


@app.cell
def _():
    qs_to_df(CourtRecord.objects.filter(person_id="b211d9a1-571f-ac92-1d95-ca235b6f1753")[:100])
    # Filtering by person_id, four (4) additional rows are found
    # Typos include: SONAY, SONY, SONA, and RESEYS
    return


@app.cell
def _():
    _df = mo.sql(
        f"""
        SELECT
            *
        FROM
            records_courtrecord
        WHERE
            LOWER(first_name) LIKE 'son%'
            AND LOWER(last_name) LIKE '%resy%'
            AND date_of_birth = '1961-05-24'
            -- Wildcards can almost find it, but only capture 17 of the 18 rows
        """,
        engine=engine
    )
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    # Phonetic algorithms

    ## Soundex and Daitch-Mokotoff
    """)
    return


@app.cell
def _():
    _df = mo.sql(
        f"""
        SELECT
            soundex ('Sonya') AS sonya,
            -- Vowel Swaps (Vowels are ignored after the first letter)
            soundex ('Sania') AS sania,
            soundex ('Senya') AS senya,
            -- Extra or Silent Letters (H, W, and trailing vowels are ignored)
            soundex ('Sohnya') AS sohnya,
            soundex ('Soniah') AS soniah,
            -- Consonant Doubles (Consecutive identical Soundex numbers merge)
            soundex ('Sonnya') AS sonnya,
            soundex ('Sonnia') AS sonnia,
            -- Phonetic Consonant Swaps ('M' and 'N' both map to the number 5)
            soundex ('Somya') AS somya,
            soundex ('Somia') AS somia,
            soundex ('Somiya') AS somiya;
        """,
        engine=engine
    )
    return


@app.cell
def _():
    _df = mo.sql(
        f"""
        SELECT
            *
        FROM
            records_courtrecord
        WHERE
            soundex (first_name) = soundex ('sonya')
            AND soundex (last_name) = soundex ('resyes')
            AND date_of_birth = '1961-05-24'
            -- A soundex() finds the typos (18 rows)
        """,
        engine=engine
    )
    return


@app.cell
def _():
    _df = mo.sql(
        f"""
        SELECT
            *
        FROM
            records_courtrecord
        WHERE
            soundex (last_name) = soundex ('resyes')
        LIMIT 10

        -- A soundex-only search returns false positives, e.g., "ROSAS" instead of "RESYES"
        """,
        engine=engine
    )
    return


@app.cell
def _():
    _df = mo.sql(
        f"""
        SELECT
            daitch_mokotoff ('michael') as michael,
            daitch_mokotoff ('mikael') as mikael,
            daitch_mokotoff ('michael') && daitch_mokotoff ('mikael') as names_match;

        -- Daitch-Mokotoff is similar, but words can have multiple sounds.
        -- We use the overlap ("&&") operator to see if they match
        """,
        engine=engine
    )
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    # Levenshtein distance and trigram matching
    """)
    return


@app.cell
def _():
    _df = mo.sql(
        f"""
        SELECT
            levenshtein ('michael', 'mikael');
        --                  ^^         ^
        -- Two (2) character changes are required to go from 'michael' to 'mikael'
        """,
        engine=engine
    )
    return


@app.cell
def _():
    _df = mo.sql(
        f"""
        SELECT
            soundex ('resyes') as soundex_resyes,
            soundex ('rosas') as soundex_rosas,
            levenshtein ('resyes', 'rosas');

        -- soundex codes match, but have a Levenshtein distance of '3'
        """,
        engine=engine
    )
    return


@app.cell(hide_code=True)
def _():
    """
    Postgres gives two ways to ask "how many edits apart are these names?".
    levenshtein() always computes the FULL distance. levenshtein_less_equal(a,
    b, max) stops early the instant the distance is known to exceed max, so a
    "within 2 edits" constraint rejects most non-matches almost immediately.
    Both return the same rows; less_equal just does less wasted CPU, and the
    gap grows as names get longer or more different. That is why the app's
    precision filter uses levenshtein_less_equal.
    """
    mo.md(r"""
    ### `levenshtein_less_equal()`

    For an efficient gain, use `levenshtein_less_equal()` instead of `levenshtein()`:

    ```sql
    -- always computes the FULL edit distance, even when it's obviously large
    SELECT levenshtein(last_name, 'SMYTH');

    -- bails out the moment the distance exceeds 2, returning a value > 2
    SELECT levenshtein_less_equal(last_name, 'SMYTH', 2);
    ```
    """)
    return


@app.cell
def _():
    _df = mo.sql(
        f"""
        SELECT
            similarity ('Geraldine', 'Gerardine') as geraldine,
            similarity ('Morgan', 'Morrison') as morgan,
        	similarity ('car', 'truck') as car,
        	similarity ('postgres', 'postgres') as pg;

        -- Compares every 3 letter slices of both words, e.g., 'ger', 'era', 'ral', etc.,
        -- and calculates the ratio of shared trigrams to total unique trigrams.
        -- Scores range from 0 to 1
        -- Two words with the same soundex code might have very low trigram similarity
        """,
        engine=engine
    )
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    ### Trigram support in Django

    `django.contrib.postgres.search` ships the trigram functions as built-in ORM
    expressions:

    - `TrigramSimilarity` -- wraps `similarity()`
    - `TrigramDistance` -- wraps the `<->` KNN distance operator
    """)
    return


@app.cell(hide_code=True)
def _():
    """
    These three live in records/expressions.py. Each is a two-line Func
    subclass: the SQL function name and the field it returns. That's the entire
    abstraction -- Django renders soundex(...), daitch_mokotoff(...), and
    levenshtein_less_equal(...) straight into the query.
    """
    mo.md(r"""
    ```python
    from django.contrib.postgres.fields import ArrayField
    from django.db.models.expressions import Func
    from django.db.models.fields import CharField, IntegerField, TextField


    class Soundex(Func):
        function = "soundex"
        output_field = CharField()


    class DaitchMokotoff(Func):
        function = "daitch_mokotoff"
        output_field = ArrayField(TextField())


    class LevenshteinLessEqual(Func):
        function = "levenshtein_less_equal"
        output_field = IntegerField()
    ```
    """)
    return


@app.cell(hide_code=True)
def _():
    """
    One line per function. Soundex groups by the code the name sounds like; DM
    matches on overlapping code arrays (that's the && behind __overlap); the
    Levenshtein is a precision filter you AND on top, never a standalone search;
    trigrams rank by distance and you take the top N. All four were run live in
    the 54M demo -- the measured row counts are under each example.
    """
    mo.md(r"""
    ## Using a function in the Django ORM

    ```python
    from django.db.models import F, Value
    from django.db.models.functions import Upper
    from .expressions import Soundex

    CourtRecord.objects.annotate(
        last_sdx=Soundex(F("last_name"))
    ).filter(last_sdx=Soundex(Value("Smyth")))
    # matches Smith, Smyth, Smythe, and Smidt -- all code S530
    ```
    """)
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    # Putting it together

    ## Using a phonetic algorithm with Levenshtein distance
    """)
    return


@app.cell
def _():
    from django.contrib.postgres.fields import ArrayField
    from django.db.models.expressions import Func, Value
    from django.db.models.functions import Upper
    from django.db.models.fields import CharField, IntegerField

    class Soundex(Func):
        function = "soundex"
        output_field = CharField()


    class DaitchMokotoff(Func):
        function = "daitch_mokotoff"
        output_field = ArrayField(TextField())


    class LevenshteinLessEqual(Func):
        function = "levenshtein_less_equal"
        output_field = IntegerField()

    return DaitchMokotoff, LevenshteinLessEqual, Upper, Value


@app.cell
def _(DaitchMokotoff, LevenshteinLessEqual, Upper, Value):
    qs_to_df(
        CourtRecord.objects.annotate(
            first_name_dm=DaitchMokotoff("first_name"),
            last_name_dm=DaitchMokotoff("last_name"),
            first_name_lev=LevenshteinLessEqual(
                Upper("first_name"), Value("SONYA"), Value(2) # case sensitive
            ),
            last_name_lev=LevenshteinLessEqual(
                Upper("last_name"), Value("RESYES"), Value(2)
            ),
        ).filter(
            first_name_dm__overlap=DaitchMokotoff(Value("Sonya")),  # case insensitive
            last_name_dm__overlap=DaitchMokotoff(Value("Resyes")),
            first_name_lev__lte=2,
            last_name_lev__lte=2,
        )
    )
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    # Database Indexes
    """)
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    ## 1. B-tree index for case-insensitive prefix matching

    ```python
    models.Index(
        OpClass(
            models.Func(models.F("last_name"), function="UPPER"),
            name="text_pattern_ops",
        ),
        OpClass(
            models.Func(models.F("first_name"), function="UPPER"),
            name="text_pattern_ops",
        ),
        name="idx_person_name_prefix",
    ),
    ```
    """)
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    ```python
    # Functional B-tree indexes for SOUNDEX equality comparisons
    models.Index(
        models.Func(models.F("first_name"), function="SOUNDEX", template="SOUNDEX(UPPER(%(expressions)s))"),
        name="idx_person_first_name_soundex",
    ),
    models.Index(
        models.Func(models.F("last_name"), function="SOUNDEX", template="SOUNDEX(UPPER(%(expressions)s))"),
        name="idx_person_last_name_soundex",
    ),
    ```
    """)
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    # Live demo!
    """)
    return


@app.cell
def _():
    mo.md(r"""
    ## Summary

    - This is a hard problem!
    - `text_pattern_ops` index for fast `__istartswith` matching
    - `soundex()` and `daitch_mokotoff()` algorithms broaden the search
    - `levenshtein_less_equal()` to narrow it again
    - Create indexes that match your _exact_ function calls
    - Postgres is awesome!
    """)
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    # Thank you for coming!

    Links:
    - Git repo: https://cakt.us/fuzzy-repo
    - Companion blog post: https://cakt.us/fuzzy-blog
    """)
    return


if __name__ == "__main__":
    app.run()
