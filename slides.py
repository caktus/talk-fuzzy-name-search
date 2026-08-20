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

    from records.models import Person


@app.cell
def _():
    """Good afternoon, everyone. Welcome to our talk."""
    # The first slide is a title slide, so we don't need to use mo.vstack() or mo.hstack() here.
    mo.md("""
    # Search-as-you-type for 54 Million Names

    ### PostgreSQL + Django for Fuzzy Name Matching at Scale

    **Caktus Group // DjangoCon US 2026**
    """)
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
    # Exploring our Demo DB
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    ```python
    class Person(models.Model):
        "A person record for fuzzy name search."

        first_name = models.CharField(max_length=50)
        last_name = models.CharField(max_length=50)
        middle_name = models.CharField(max_length=50, blank=True)
        date_of_birth = models.DateField()
        # Often one doesn't have a cannonical person_id to
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
        Person.objects.filter(
            first_name__iexact="amanda",
            last_name__iexact="morgan",
        )[:1000]
    )
    # All persons named "Amanda Morgan" (646 rows, 389 unique persons)
    return


@app.cell
def _():
    qs_to_df(
        Person.objects.filter(
            first_name__icontains="amanda",
            last_name__icontains="morgan",
            date_of_birth="1993-10-02",
        )[:100]
    )
    # Five (5) records for Amanda Morgan born 1993-10-02
    return


@app.cell
def _():
    qs_to_df(Person.objects.filter(person_id="d2a81060-fde2-8f0b-71ba-aa32e474a46b")[:100])
    # Filtering by person_id, one additional row for "AMAANDA" is found
    return


@app.cell
def _():
    _df = mo.sql(
        f"""
        SELECT
            *
        FROM
            records_person
        WHERE
            LOWER(first_name) LIKE '%am%nda%'
            AND LOWER(last_name) LIKE '%morgan%'
            AND date_of_birth = '1993-10-02'
            -- A wildcard in place of the double 'a' finds the row, but is impractical
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
            soundex ('Amanda') AS amanda,
            -- Vowel Swaps (Vowels are ignored after the first letter)
            soundex ('Aminda') AS aminda,
            soundex ('Amenda') AS amenda,
            -- Extra or Silent Letters (H, W, and trailing vowels are ignored)
            soundex ('Ahmanda') AS ahmanda,
            soundex ('Amandie') AS amandie,
            -- Consonant Doubles (Consecutive identical Soundex numbers merge)
            soundex ('Ammanda') AS ammanda,
            soundex ('Amannda') AS amannda,
            -- Phonetic Consonant Swaps ('D' and 'T' both map to the number 3)
            soundex ('Amanta') AS amanta,
            soundex ('Amantha') AS amantha,
            soundex ('Amentha') AS amentha;
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
            records_person
        WHERE
            soundex (first_name) = soundex ('amanda')
            AND LOWER(last_name) LIKE '%morgan%'
            AND date_of_birth = '1993-10-02'
            -- A soundex() test on first_name finds the typo
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
            records_person
        WHERE
            soundex (first_name) = soundex ('amanda')
            AND soundex (last_name) = soundex ('morgan')
            AND lower(last_name) != 'morgan'

        -- A soundex-only search returns false positives, e.g., "MORRISON" instead of "MORGAN"
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
            soundex ('morgan') as soundex_morgan,
            soundex ('morrison') as soundex_morrison,
            levenshtein ('morgan', 'morrison');

        -- soundex codes match, but have a Levenshtein distance of '4'
        """,
        engine=engine
    )
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
    ### In Django, you don't write that SQL

    `django.contrib.postgres.search` ships the trigram functions as built-in ORM
    expressions:

    - `TrigramSimilarity` — wraps `similarity()`
    - `TrigramDistance` — wraps the `<->` KNN distance operator

    The demo's `Person.objects.search_trigram()` / `search_unified(["trigram"], …)`
    use `TrigramDistance` under the hood (see `records/models.py`) — no raw SQL.
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
    _df = mo.sql(
        f"""
        SELECT
            *
        FROM
            records_person
        WHERE
            -- Find similar-sounding first and last names
            daitch_mokotoff (upper(first_name)) && daitch_mokotoff ('AMANDA')
            AND daitch_mokotoff (upper(last_name)) && daitch_mokotoff ('MORGAN')
            -- Allow up to two character differences
            -- AND levenshtein (upper(first_name), 'AMANDA') <= 2
            -- AND levenshtein (last_name, 'MORGAN') <=2
            AND levenshtein_less_equal (upper(first_name), 'AMANDA', 2) <= 2
            AND levenshtein_less_equal (last_name, 'MORGAN', 2) <= 2
            -- Exclude exact matches to make typos more visible
            --AND last_name != 'MORGAN'
            -- With this (indexed) query we can find 11 similar typos in 54,000,000 rows in <40ms!
            -- Postgres is amazing :)
        """,
        engine=engine
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


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    # Thank you for coming!

    Links:
    - Git repo (Django project, sample data, these slides): https://github.com/caktus/talk-fuzzy-name-search
    - Caktus Group: https://www.caktusgroup.com/
    """)
    return


if __name__ == "__main__":
    app.run()
