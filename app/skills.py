"""One agreed spelling per skill. Plain code, no AI.

Every skill name from Gemma (CV, dream jobs, job tags, projects) goes through
`canonical()` before it is stored or compared. Without this, "React.js" on a CV and
"React" in a job advert would not match and ranking would be silently wrong.
"""
import re

# canonical spelling -> other spellings people use (all lower case)
_ALIASES = {
    "Python": ["python3", "python 3"],
    "JavaScript": ["js", "javascript (es6)", "es6", "ecmascript", "vanilla js"],
    "TypeScript": ["ts"],
    "Java": ["java 8", "java 11", "java 17", "core java"],
    "C#": ["c sharp", "csharp", ".net c#"],
    "C++": ["cpp", "c plus plus"],
    "C": [],
    "Go": ["golang"],
    "Rust": [],
    "Kotlin": [],
    "Swift": [],
    "PHP": [],
    "Ruby": ["ruby on rails", "rails", "ror"],
    "SQL": ["t-sql", "pl/sql", "plsql", "sql queries"],
    "React": ["reactjs", "react.js", "react js", "react native web"],
    "Angular": ["angularjs", "angular.js", "angular 2+"],
    "Vue": ["vuejs", "vue.js", "vue js"],
    "Node.js": ["node", "nodejs", "node js"],
    "Express": ["expressjs", "express.js"],
    "Django": [],
    "Flask": [],
    "FastAPI": ["fast api"],
    "Spring Boot": ["springboot", "spring", "spring framework"],
    ".NET": ["dotnet", "dot net", ".net core", "asp.net", "asp.net core", "net core"],
    "HTML": ["html5", "html/css"],
    "CSS": ["css3", "scss", "sass"],
    "Tailwind CSS": ["tailwind", "tailwindcss"],
    "REST APIs": ["rest", "restful", "restful apis", "rest api", "restful api", "rest apis", "apis", "api development"],
    "GraphQL": [],
    "PostgreSQL": ["postgres", "postgresql database", "psql"],
    "MySQL": [],
    "MongoDB": ["mongo", "mongo db"],
    "Redis": [],
    "SQLite": [],
    "AWS": ["amazon web services", "aws cloud", "amazon aws"],
    "Azure": ["microsoft azure", "azure cloud"],
    "GCP": ["google cloud", "google cloud platform"],
    "Docker": ["containers", "containerisation", "containerization", "docker compose"],
    "Kubernetes": ["k8s"],
    "Terraform": [],
    "CI/CD": ["ci cd", "cicd", "continuous integration", "continuous delivery",
              "continuous deployment", "ci/cd pipelines", "github actions", "jenkins", "gitlab ci"],
    "Git": ["github", "gitlab", "version control", "git version control", "bitbucket"],
    "Linux": ["unix", "bash", "shell scripting", "linux/unix"],
    "Testing": ["unit testing", "unit tests", "test driven development", "tdd",
                "automated testing", "test automation", "jest", "pytest", "junit", "qa"],
    "Agile": ["scrum", "kanban", "agile methodologies", "agile/scrum"],
    "Data Structures & Algorithms": ["data structures", "algorithms", "dsa", "data structures and algorithms"],
    "Object-Oriented Programming": ["oop", "object oriented programming", "object-oriented design",
                                    "object oriented design"],
    "Microservices": ["micro services", "microservice architecture"],
    "System Design": ["systems design", "software architecture", "architecture"],
    "Machine Learning": ["ml", "machine-learning"],
    "Data Analysis": ["data analytics", "analytics"],
    "Pandas": [],
    "NumPy": ["numpy"],
    "TensorFlow": [],
    "PyTorch": [],
    "Kafka": ["apache kafka"],
    "RabbitMQ": [],
    "Security": ["cyber security", "cybersecurity", "application security", "secure coding"],
    "Networking": ["tcp/ip", "computer networks"],
    "Android": ["android development"],
    "iOS": ["ios development"],
    "Communication": ["communication skills", "teamwork", "collaboration", "stakeholder management"],
    "Problem Solving": ["problem-solving", "analytical skills", "analytical thinking"],
}

_LOOKUP = {}
for _name, _others in _ALIASES.items():
    _LOOKUP[_name.lower()] = _name
    for _o in _others:
        _LOOKUP[_o.lower()] = _name

# Soft skills are real but make poor "gaps" and poor ranking signals.
SOFT_SKILLS = {"Communication", "Problem Solving"}


def canonical(name):
    """Return the one agreed spelling for a skill name ('' if the name is empty)."""
    if not isinstance(name, str):
        return ""
    cleaned = re.sub(r"\s+", " ", name).strip()
    found = _LOOKUP.get(cleaned.lower())
    if found:
        return found
    cleaned = cleaned.strip(".,;:()[]\"'")
    if not cleaned:
        return ""
    found = _LOOKUP.get(cleaned.lower())
    if found:
        return found
    # Unknown skill: keep it, but tidy the case so "KUBEFLOW" and "kubeflow" match.
    if cleaned.islower() or cleaned.isupper():
        return cleaned.upper() if len(cleaned) <= 4 else cleaned.title()
    return cleaned[:60]


def canonical_list(names, limit=40):
    """Clean a list of skill names: canonical spelling, no blanks, no duplicates, order kept."""
    out = []
    for n in names or []:
        c = canonical(n)
        if c and c not in out:
            out.append(c)
    return out[:limit]


def known_skills():
    """Every canonical skill name the app knows about."""
    return list(_ALIASES.keys())


def _pattern(term):
    # Letters on both sides disqualify a match ("go" inside "going"); symbols like + # . are fine.
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", re.IGNORECASE)


# Short or ambiguous words are only counted when written in a recognisable form.
_CASE_SENSITIVE = {"Go": ["Go", "Golang"], "C": ["C"], "Swift": ["Swift"], "Rust": ["Rust"],
                   "Git": ["Git"], "Ruby": ["Ruby"], "Spring Boot": ["Spring Boot", "Spring"]}
_SKIP_TERMS = {"qa", "rest", "node", "ts", "js", "ml", "spring", "rails", "mongo", "containers",
               "architecture", "analytics", "apis", "collaboration", "teamwork", "unix", "bash",
               "security", "networking", "testing", "ios development", "android development"}
# Hand-written patterns for names that are also ordinary text or prefixes of other names.
_SPECIAL = {
    "C": re.compile(r"(?<![A-Za-z0-9.#+])C(?=\s?[,/)]|\s+(?:and|or)\s+C|\s+(?:programming|language|developer))"
                    r"|(?<=[,/(])\s?C(?![A-Za-z0-9#+])"),
    "Go": re.compile(r"Golang|(?<=[,/(])\s?Go(?![A-Za-z0-9])|(?<![A-Za-z0-9])Go(?=\s?[,/)])"),
}

_PATTERNS = None


def _build_patterns():
    pats = [(name, pat) for name, pat in _SPECIAL.items()]
    for name, others in _ALIASES.items():
        if name in _SPECIAL:
            continue
        terms = [name] + others
        for t in terms:
            if name in _CASE_SENSITIVE:
                if t not in _CASE_SENSITIVE[name]:
                    continue
                pats.append((name, re.compile(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])")))
            elif t.lower() not in _SKIP_TERMS:
                pats.append((name, _pattern(t)))
    return pats


def find_skills(text):
    """Scan free text for known skills. Returns [(canonical_name, position_of_first_match)]."""
    global _PATTERNS
    if _PATTERNS is None:
        _PATTERNS = _build_patterns()
    hits = {}
    for name, pat in _PATTERNS:
        m = pat.search(text or "")
        if m and (name not in hits or m.start() < hits[name]):
            hits[name] = m.start()
    return sorted(hits.items(), key=lambda kv: kv[1])
