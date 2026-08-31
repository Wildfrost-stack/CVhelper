import os
import re
import json
from typing import Optional
from dotenv import load_dotenv
from groq import Groq

# Automatically load environment variables from .env file
load_dotenv()


def get_groq_client():
    """Dynamically fetches the API key from environment variables without crashing the process."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("Missing API Key! Please set GROQ_API_KEY in your environment.")
    # qwen/qwen3.6-27b is currently a Groq *preview* model and can be slower
    # / less consistent than GA models, so give it more headroom than the
    # SDK default (and a couple of automatic retries on transient errors).
    return Groq(api_key=api_key, timeout=60.0, max_retries=2)


class PrivacyAgentError(Exception):
    """Raised when PII redaction could not be completed — callers must
    treat this as fatal and stop, never fall back to the raw text."""
    pass


def strip_thinking(text: str) -> str:
    """
    Defensive fallback: remove any <think>...</think> block that a reasoning
    model may leak into `content`, even when reasoning_format='hidden' is set.
    Also handles an unclosed <think> tag (e.g. if the response got cut off
    mid-reasoning due to max_tokens) by dropping everything from <think> onward.
    """
    if not text:
        return text

    # Remove complete <think>...</think> blocks
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # If an unclosed <think> tag remains (truncated response), drop from there on
    cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.DOTALL)

    return cleaned.strip()


def run_privacy_agent(raw_text: str) -> str:
    """
    AGENT 1: Data Privacy Compliance Officer.

    This must fail CLOSED. If redaction can't be completed, we raise
    PrivacyAgentError instead of returning raw_text — this app's entire
    premise is that PII never reaches the scoring model or the database,
    so silently falling back to the unredacted input would be worse than
    just failing the request.
    """
    system_prompt = """
    You are a strict Data Privacy Compliance Officer. Your ONLY job is to take the 
    user's input and redact all Personally Identifiable Information (PII).
    Replace names with [NAME], emails with [EMAIL], phone numbers with [PHONE], and locations with [LOCATION].
    Do NOT evaluate the resume. ONLY return the redacted text.
    """

    last_error: Optional[Exception] = None
    for attempt in range(2):  # one retry in case of a transient timeout
        try:
            client = get_groq_client()
            response = client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                temperature=0.0,
                max_tokens=2048,
                reasoning_format="hidden",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": raw_text}
                ]
            )
            content = strip_thinking(response.choices[0].message.content)
            if content and content.strip():
                return content
            last_error = ValueError("Privacy agent returned empty output")
        except Exception as e:
            last_error = e
            print(f"[Privacy Agent attempt {attempt + 1} failed]: {e}")

    raise PrivacyAgentError(f"PII redaction failed after retries: {last_error}")


def run_auditor_agent(redacted_text: str, submission_type: str = "resume") -> str:
    """AGENT 2: Technical Lead & ATS Auditor"""
    try:
        client = get_groq_client()
        system_prompt = f"""
        You are an expert HR Recruiter and ATS Evaluator evaluating a {submission_type}.
        Analyze the submission purely on merit and formatting.

        Structure your response strictly as follows:

        ## 1. Executive Summary
        - **Overall Score**: [X]/10
        - **Decision**: [ACCEPTED / REJECTED / UNDER_REVIEW]

        ## 2. ATS Compatibility & Structure
        | Aspect | Status | Assessment & Notes |
        | :--- | :--- | :--- |
        | File Format | Pass | Text structure extractable |
        | Contact Info | Pass | Placeholder elements detected |
        | Keyword Density | Action Needed | Lacks role-specific terminology |

        ## 3. Key Strengths
        * **Quantified Achievements**: Strong use of metrics in experience items.
        * **Action Verbs**: Led, developed, optimized utilized effectively.

        ## 4. Identified Weaknesses
        * **Formatting Consistency**: Inconsistent bullet point spacing.
        * **Unfilled Placeholders**: Blank contact fields remain in document body.

        ## 5. Actionable Recommendations
        1. Standardize bullet margins and list alignment across sections.
        2. Add role-specific framework keywords to improve ATS indexing.
        3. Replace unpopulated contact details with finalized information.
        """
        response = client.chat.completions.create(
            model="qwen/qwen3.6-27b",
            temperature=0.0,
            max_tokens=2048,
            reasoning_format="hidden",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": redacted_text}
            ]
        )
        content = strip_thinking(response.choices[0].message.content)
        return content if content and content.strip() else "Audit evaluation could not be generated."
    except Exception as e:
        print(f"[Auditor Agent Exception Handled]: {e}")
        return f"## 1. Executive Summary\n- **Overall Score**: 0/10\n- **Decision**: UNDER_REVIEW\n\n*Error executing audit agent: {str(e)}*"


def run_scoring_agent(redacted_text: str, submission_type: str = "resume") -> dict:
    """
    AGENT 2b: Structured Scoring Agent.

    The frontend's score bars just need `{"labels": [...], "scores": [...]}`
    (4 categories, 0-100 each) — this is a small, separate call from
    run_auditor_agent so we get clean JSON back instead of having to parse
    numbers out of a markdown report.
    """
    default = {
        "labels": ["Skill Match", "Experience Relevance", "Clarity", "Impact Evidence"],
        "scores": [60, 60, 60, 60],
    }
    try:
        client = get_groq_client()
        system_prompt = f"""
        You are scoring a {submission_type} on four dimensions, each 0-100.
        Respond with ONLY a single JSON object and nothing else — no prose,
        no markdown code fences. Use exactly this shape:
        {{"labels": ["Skill Match", "Experience Relevance", "Clarity", "Impact Evidence"], "scores": [0, 0, 0, 0]}}
        """
        response = client.chat.completions.create(
            model="qwen/qwen3.6-27b",
            temperature=0.0,
            max_tokens=256,
            reasoning_format="hidden",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": redacted_text}
            ]
        )
        content = strip_thinking(response.choices[0].message.content)
        if not content:
            return default

        # Defensive: strip accidental ```json fences if the model adds them anyway
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()

        data = json.loads(content)
        labels = data.get("labels")
        scores = data.get("scores")
        if not labels or not scores or len(labels) != len(scores):
            return default

        clean_scores = [max(0, min(100, round(float(s)))) for s in scores]
        return {"labels": labels, "scores": clean_scores}
    except Exception as e:
        print(f"[Scoring Agent Exception Handled]: {e}")
        return default


def run_interview_coach_agent(redacted_text: str) -> str:
    """AGENT 3: Technical Interview Preparation Coach"""
    try:
        client = get_groq_client()
        system_prompt = """
        You are an expert Technical Interview Coach.
        Analyze the candidate's skills and projects in the provided text.
        Generate 3 targeted technical interview questions and 3 key conceptual topics 
        the candidate should study to prepare for interviews in this field.
        Format cleanly in Markdown.
        """
        response = client.chat.completions.create(
            model="qwen/qwen3.6-27b",
            temperature=0.3,
            max_tokens=2048,
            reasoning_format="hidden",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": redacted_text}
            ]
        )
        content = strip_thinking(response.choices[0].message.content)
        return content if content and content.strip() else "Interview coaching guide could not be generated."
    except Exception as e:
        print(f"[Interview Coach Agent Exception Handled]: {e}")
        return f"*Error executing Interview Coach Agent: {str(e)}*"