import spacy
from spacy.matcher import DependencyMatcher

class SpacyExtractor:
    def __init__(self):
        self.nlp = spacy.load("en_core_web_sm")

    def extract_table(self, doc):
        # Find the direct object of verbs like "show", "list"
        for token in doc:
            if token.pos_ == "VERB" and token.lemma_ in ("show", "list", "find", "select"):
                for child in token.children:
                    if child.dep_ == "dobj" and child.pos_ == "NOUN":
                        return child.lemma_.lower()
        # Fallback: noun after "from"
        for i, token in enumerate(doc):
            if token.lower_ == "from" and i+1 < len(doc):
                return doc[i+1].lemma_.lower()
        return None

    def extract_condition(self, doc):
        # Traverse the dependency tree to find comparisons
        conditions = []
        for token in doc:
            if token.dep_ in ("acomp", "advcl", "relcl") and token.pos_ == "ADJ":
                # e.g., "age less than 5" – the adjective "less" is attached to a number
                # This is more advanced – we can start simple
                pass
        # For now, return None – let the rule‑based extractor handle it
        return None