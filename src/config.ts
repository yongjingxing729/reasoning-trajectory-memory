/** Plugin options: Cordis profile config for the reasoning-memory bundle.
 *  The interface documents the shape; the schemastery schema supplies defaults
 *  and range validation. Kept separate from the entry so tool modules and
 *  layers can depend on the option type without importing the plugin root. */
import z from '@deepseek-ai/schemastery'

export interface Config {
  pythonExecutable?: string
  maxMemories?: number
  maxRetrievalRecords?: number
  maxTrajectoryEvents?: number
  positiveNegativeRatio?: number
  vectorIndexEnabled?: boolean
  qdrantUrl?: string
  qdrantCollection?: string
  qdrantApiKeyEnv?: string
  embeddingUrl?: string
  embeddingModel?: string
  embeddingDimension?: number
  embeddingApiKeyEnv?: string
  retrievalCandidatePoolSize?: number
  vectorCandidatePoolSize?: number
  enterpriseRuleRagEnabled?: boolean
  enterpriseRuleRagRequired?: boolean
  enterpriseRuleCollection?: string
  enterpriseRuleSetId?: string
  enterpriseRuleCandidatePoolSize?: number
  enterpriseRuleResultLimit?: number
  enterpriseRuleScoreThreshold?: number
  enterpriseRuleEmbeddingUrl?: string
  enterpriseRuleEmbeddingModel?: string
  enterpriseRuleEmbeddingDimension?: number
  enterpriseRuleQdrantApiKeyEnv?: string
  enterpriseRuleEmbeddingApiKeyEnv?: string
  captureReasoningEnabled?: boolean
  maxReasoningTextChars?: number
  llmDistillationEnabled?: boolean
  llmDistillationTemperature?: number
  distillationProvider?: string
  distillationModel?: string
  maxMemoryItemsPerOutcome?: number
  deSpecificationThreshold?: number
  relevanceThreshold?: number
  memoryConsolidationEnabled?: boolean
  builtinStoreEnabled?: boolean
  builtinStoreDataDir?: string
  apiConfigPath?: string
  skillsExportDir?: string
  minInsightConfidence?: number
  deprecatedAfterHarmfulFeedback?: number
  templateMatchMin?: number
  documentCollection?: string
  documentChunkTargetChars?: number
  documentChunkMinChars?: number
  documentMaxHops?: number
  quantumDecisionEnabled?: boolean
  maxSuperpositionSize?: number
  phaseFromCausal?: boolean
  decoherenceRate?: number
  distillGateEnabled?: boolean
  distillUpliftThreshold?: number
  phaseFromDistillUplift?: boolean
  distillShrinkPrior?: number
  distillMinArmSamples?: number
}

const defaultPython = process.platform === 'win32' ? 'python' : 'python3'

export const Config: z<Config> = z.object({
  pythonExecutable: z.string().default(defaultPython),
  maxMemories: z.number().min(1).default(5000),
  maxRetrievalRecords: z.number().min(1).default(10000),
  maxTrajectoryEvents: z.number().min(10).default(1000),
  positiveNegativeRatio: z.number().min(0.1).max(10).default(1),
  vectorIndexEnabled: z.boolean().default(false),
  qdrantUrl: z.string().default('http://127.0.0.1:6333'),
  qdrantCollection: z.string().default('reasoning_memories'),
  qdrantApiKeyEnv: z.string().default('QDRANT_API_KEY'),
  embeddingUrl: z.string().default('https://api.openai.com/v1/embeddings'),
  embeddingModel: z.string().default('text-embedding-3-small'),
  embeddingDimension: z.number().min(1).default(1536),
  embeddingApiKeyEnv: z.string().default('OPENAI_API_KEY'),
  retrievalCandidatePoolSize: z.number().min(10).max(1000).default(100),
  vectorCandidatePoolSize: z.number().min(10).max(1000).default(100),
  enterpriseRuleRagEnabled: z.boolean().default(false),
  enterpriseRuleRagRequired: z.boolean().default(false),
  enterpriseRuleCollection: z.string().default('enterprise_reasoning_rules'),
  enterpriseRuleSetId: z.string().default(''),
  enterpriseRuleCandidatePoolSize: z.number().min(1).max(100).default(20),
  enterpriseRuleResultLimit: z.number().min(1).max(10).default(5),
  enterpriseRuleScoreThreshold: z.number().min(0).max(1).default(0.3),
  enterpriseRuleEmbeddingUrl: z.string().default(''),
  enterpriseRuleEmbeddingModel: z.string().default(''),
  enterpriseRuleEmbeddingDimension: z.number().min(0).default(0),
  enterpriseRuleQdrantApiKeyEnv: z.string().default('ENTERPRISE_RULES_QDRANT_API_KEY'),
  enterpriseRuleEmbeddingApiKeyEnv: z.string().default('ENTERPRISE_RULES_EMBEDDING_API_KEY'),
  captureReasoningEnabled: z.boolean().default(false),
  maxReasoningTextChars: z.number().min(100).max(40000).default(4000),
  llmDistillationEnabled: z.boolean().default(false),
  llmDistillationTemperature: z.number().min(0).max(2).default(1),
  distillationProvider: z.string().default(''),
  distillationModel: z.string().default(''),
  maxMemoryItemsPerOutcome: z.number().min(1).max(3).default(3),
  deSpecificationThreshold: z.number().min(0).max(1).default(0.9),
  relevanceThreshold: z.number().min(0).max(1).default(0),
  memoryConsolidationEnabled: z.boolean().default(false),
  builtinStoreEnabled: z.boolean().default(false),
  builtinStoreDataDir: z.string().default(''),
  apiConfigPath: z.string().default(''),
  skillsExportDir: z.string().default(''),
  minInsightConfidence: z.number().min(0).max(1).default(0),
  deprecatedAfterHarmfulFeedback: z.number().min(0).default(0),
  templateMatchMin: z.number().min(0).max(1).default(0.55),
  documentCollection: z.string().default('reasoning_documents'),
  documentChunkTargetChars: z.number().min(100).default(1200),
  documentChunkMinChars: z.number().min(1).default(300),
  documentMaxHops: z.number().min(0).max(2).default(2),
  quantumDecisionEnabled: z.boolean().default(false),
  maxSuperpositionSize: z.number().min(1).max(3).default(3),
  phaseFromCausal: z.boolean().default(true),
  decoherenceRate: z.number().min(0).max(1).default(0),
  distillGateEnabled: z.boolean().default(false),
  distillUpliftThreshold: z.number().min(-1).max(1).default(0),
  phaseFromDistillUplift: z.boolean().default(true),
  distillShrinkPrior: z.number().min(1).default(20),
  distillMinArmSamples: z.number().min(1).default(5),
})

