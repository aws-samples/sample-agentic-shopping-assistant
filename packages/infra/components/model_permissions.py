"""Approve one configured model, including its inference profile destinations."""
def model_resources(model_id: str, region: str, account: str, partition="aws"):
    if "*" in model_id or "?" in model_id or not model_id:
        raise ValueError("Model permissions require an exact model ID")
    if model_id.startswith("arn:"):
        return [model_id]
    prefix, _, foundation = model_id.partition(".")
    if prefix in ("global", "us", "eu", "apac"):
        return [f"arn:{partition}:bedrock:{region}:{account}:inference-profile/{model_id}", f"arn:{partition}:bedrock:*::foundation-model/{foundation}"]
    return [f"arn:{partition}:bedrock:{region}::foundation-model/{model_id}"]
