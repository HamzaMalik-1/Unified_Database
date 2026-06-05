def _llm_fallback(self, text: str) -> Optional[str]:
    """
    First attempt: fine‑tuned model (no few‑shot).
    If that fails, fallback to few‑shot with base model.
    """
    # ------------------------------------------------------------------
    # Step 1: Try fine‑tuned model (without few‑shot)
    # ------------------------------------------------------------------
    ft_command = self._call_finetuned_model(text)
    if ft_command and ft_command.startswith(("SQL ", "NOSQL ")):
        print("Using fine‑tuned model")
        return ft_command

    # ------------------------------------------------------------------
    # Step 2: Fallback to few‑shot with base model
    # ------------------------------------------------------------------
    print("Falling back to few‑shot with base model")
    return self._few_shot_fallback(text)

def _call_finetuned_model(self, text: str) -> Optional[str]:
    """
    Call the fine‑tuned Ollama model with schema injection.
    Returns a command or None.
    """
    # Get current schema
    schema_str = self._get_current_schema_as_string()
    if not schema_str:
        schema_str = "SQL tables: none. NoSQL collections: none."

    # Build minimal prompt (no few‑shot)
    prompt = f"### Schema: {schema_str}\n### Question: {text}\n### Command:"

    try:
        result = subprocess.run(
            ["ollama", "run", self.FINETUNED_MODEL],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.OLLAMA_TIMEOUT,
            encoding='utf-8'
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    raw = result.stdout.strip()
    # Extract first line that looks like a command
    for line in raw.split('\n'):
        line = line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith(("SQL ", "NOSQL ")):
            return line
        if upper.startswith(("SELECT", "INSERT", "UPDATE", "DELETE",
                             "CREATE", "ALTER", "DROP", "WITH", "CALL")):
            return f"SQL {line}"
    return None

def _few_shot_fallback(self, text: str) -> Optional[str]:
    """
    Few‑shot fallback using base model + random examples from train.jsonl.
    (Your existing code, slightly adapted.)
    """
    # ------------------------------------------------------------------
    # 1. Load and cache few‑shot examples
    # ------------------------------------------------------------------
    if not hasattr(self, '_few_shot_examples'):
        dataset_path = Path(__file__).parent.parent / "dataset" / "train.jsonl"
        if not dataset_path.exists():
            print(f"[NLP] Dataset not found: {dataset_path}", file=sys.stderr)
            return None
        with open(dataset_path, 'r', encoding='utf-8') as f:
            self._few_shot_examples = [json.loads(line) for line in f if line.strip()]
        if not self._few_shot_examples:
            print("[NLP] No valid examples in dataset", file=sys.stderr)
            return None

    # ------------------------------------------------------------------
    # 2. Pick 1‑2 random examples
    # ------------------------------------------------------------------
    num_shots = min(2, len(self._few_shot_examples))
    few_shot = random.sample(self._few_shot_examples, num_shots)

    # ------------------------------------------------------------------
    # 3. Build schema description
    # ------------------------------------------------------------------
    schema_str = self._get_current_schema_as_string()
    if not schema_str:
        schema_str = "SQL tables: none. NoSQL collections: none."

    # ------------------------------------------------------------------
    # 4. Build prompt (strong instruction)
    # ------------------------------------------------------------------
    prompt_parts = []
    prompt_parts.append("You are a database assistant. Output ONLY the command – no explanation, no markdown. The command MUST start with 'SQL ' or 'NOSQL '.\n")
    for ex in few_shot:
        prompt_parts.append(
            f"### Schema: {ex['schema']}\n"
            f"### Question: {ex['question']}\n"
            f"### Command: {ex['output']}\n"
        )
    prompt_parts.append(
        f"### Schema: {schema_str}\n"
        f"### Question: {text}\n"
        f"### Command:"
    )
    full_prompt = "\n".join(prompt_parts)

    # ------------------------------------------------------------------
    # 5. Call base Ollama model
    # ------------------------------------------------------------------
    try:
        result = subprocess.run(
            ["ollama", "run", self.BASE_MODEL],
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=self.OLLAMA_TIMEOUT,
            encoding='utf-8'
        )
    except subprocess.TimeoutExpired:
        print("[NLP] Few‑shot timeout", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[NLP] Few‑shot error: {e}", file=sys.stderr)
        return None

    if result.returncode != 0:
        print(f"[NLP] Base model error: {result.stderr}", file=sys.stderr)
        return None

    # ------------------------------------------------------------------
    # 6. Extract raw command and add prefix if missing
    # ------------------------------------------------------------------
    raw_output = result.stdout.strip()
    raw_command = None
    for line in raw_output.split('\n'):
        line_clean = line.strip()
        if not line_clean:
            continue
        upper = line_clean.upper()
        if upper.startswith(("SQL ", "NOSQL ")):
            return line_clean.rstrip(';')
        if upper.startswith(("SELECT", "INSERT", "UPDATE", "DELETE",
                             "CREATE", "ALTER", "DROP", "WITH", "CALL",
                             "NOSQL", "CONTAINS", "PUSH", "PULL", "ADDTOSET")):
            raw_command = line_clean
            break

    if not raw_command:
        raw_command = raw_output.split('\n')[0].strip() if raw_output else None
        if not raw_command:
            return None

    # Determine prefix (SQL or NOSQL) – simplified version
    import re
    # Check for NoSQL keywords first
    if re.search(r'\b(CONTAINS|PUSH|PULL|ADDTOSET)\b', raw_command, re.IGNORECASE):
        prefix = "NOSQL"
    else:
        # Try to extract table name
        table_match = re.search(r'\bFROM\s+([a-z_][a-z0-9_]*)', raw_command, re.IGNORECASE)
        if table_match:
            table = table_match.group(1)
            prefix = "SQL" if table in self.get_sql_tables() else "NOSQL"
        else:
            prefix = "SQL"
    return f"{prefix} {raw_command.rstrip(';')}"