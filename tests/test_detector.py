from namr.detector import is_default_title


def test_english_defaults_detected():
    cases = [
        "Morning Run",
        "Lunch Ride",
        "Afternoon Swim",
        "Evening Walk",
        "Night Hike",
        "Morning Weight Training",
        "Morning Workout",
        "Morning Activity",
        "Morning Trail Run",
        "Pool Swim",           # standalone (no time prefix)
        "Morning Pool Swim",
        "pool swim",
        "morning run",         # case-insensitive
        " Morning   Run ",     # whitespace tolerant
    ]
    for c in cases:
        assert is_default_title(c), c


def test_spanish_defaults_detected():
    cases = [
        "Carrera matinal",
        "Carrera del mediodía",
        "Carrera vespertina",
        "Carrera nocturna",
        "Carrera de la mañana",
        "Carrera de la noche",
        "Vuelta en bici matinal",
        "Paseo matinal",
        "Excursión matinal",
        "Natación matinal",
        "Natación de la mañana",
        "Natación de mañana",      # locale variant without article
        "Natación de la tarde",
        "Paseo de la mañana",
        "Caminata de mañana",      # LatAm prepositional, no article
        "Caminata de noche",
        "Caminata de la tarde",
        "Caminata matinal",        # adjective form, kept defensively
        "Excursión de la noche",
        "Entrenamiento matinal",
        "Entrenamiento de la mañana",
        "Actividad matinal",
        "Actividad de la tarde",
        "Vuelta en bici de la mañana",
        "Salida en bici matinal",
        "carrera matinal",       # case-insensitive
        "Carrera del mediodia",  # accent-insensitive
    ]
    for c in cases:
        assert is_default_title(c), c


def test_catalan_defaults_detected():
    cases = [
        "Cursa matinal",
        "Cursa del migdia",
        "Cursa vespertina",
        "Cursa nocturna",
        "Cursa al matí",
        "Cursa a la nit",
        "Sortida en bici matinal",
        "Passejada matinal",
        "Excursió matinal",
        "Activitat matinal",
        "cursa matinal",
        "Cursa al mati",  # accent-insensitive
    ]
    for c in cases:
        assert is_default_title(c), c


def test_custom_titles_not_detected():
    cases = [
        "Sweaty laps round Parc de la Ciutadella",
        "Wind in the face, gravel in the gears",
        "Ten by 400 on the track",
        "Sunday slow shuffle",
        "Quick spin before lunch",
        "Z2 con Marc por Collserola",
        "Pujada al Tibidabo",
        "Race day — Barcelona Half",
    ]
    for c in cases:
        assert not is_default_title(c), c


def test_empty_or_none_treated_as_default():
    assert is_default_title("")
    assert is_default_title(None)
    assert is_default_title("   ")
