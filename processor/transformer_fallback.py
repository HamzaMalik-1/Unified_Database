from transformers import T5ForConditionalGeneration, T5Tokenizer
import torch

class TextToSQL:
    def __init__(self, model_name="mrm8488/t5-base-finetuned-wikiSQL"):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.tokenizer = T5Tokenizer.from_pretrained(model_name)
        self.model = T5ForConditionalGeneration.from_pretrained(model_name).to(self.device)
        self.model.eval()

    def generate(self, text: str) -> str:
        input_text = f"translate English to SQL: {text}"
        inputs = self.tokenizer(input_text, return_tensors="pt", truncation=True, max_length=512).to(self.device)
        with torch.no_grad():
            outputs = self.model.generate(inputs.input_ids, max_length=200, num_beams=4, early_stopping=True)
        sql = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
        # Ensure the command starts with "SQL"
        if not sql.upper().startswith("SQL"):
            sql = f"SQL {sql}"
        return sql