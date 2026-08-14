from transformers import AutoTokenizer

# 加载多语言 BERT 分词器
tokenizer = AutoTokenizer.from_pretrained("bert-base-multilingual-cased")

# 中文文本
text_zh = "你好，世界！今天天气真好。你今天过得怎么样？"

# 英文文本
text_en = "Natural language processing is a fascinating field."

# 对中文进行分词
tokens_zh = tokenizer.tokenize(text_zh)
print("中文分词结果：", tokens_zh)

# 对英文进行分词
tokens_en = tokenizer.tokenize(text_en)
print("英文分词结果：", tokens_en)

# 如果你还需要编码（token IDs），可以使用 tokenizer.convert_tokens_to_ids
# 或者直接使用 tokenizer(text) 来获取 token IDs 和 attention masks
encoded_input = tokenizer(text_zh, return_tensors="pt", is_split_into_words=True)
print("编码结果：", encoded_input)
