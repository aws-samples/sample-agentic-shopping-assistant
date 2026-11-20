import type { Product } from "~/types/domain";

export function isValidProduct(product: any): product is Product {
  return (
    product &&
    typeof product === "object" &&
    typeof product.id === "string" &&
    typeof product.gender === "string" &&
    typeof product.masterCategory === "string" &&
    typeof product.subCategory === "string" &&
    typeof product.articleType === "string" &&
    typeof product.baseColour === "string" &&
    typeof product.season === "string" &&
    typeof product.year === "number" &&
    typeof product.usage === "string" &&
    typeof product.productDisplayName === "string"
  );
}
