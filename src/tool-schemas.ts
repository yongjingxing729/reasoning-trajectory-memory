/** Tool output schemas: the model-facing JSON shape of every memory_* result.
 *  Pure constants — no imports, no state. */

export const REASONING_GRAPH_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    version: { type: 'integer', required: true, enum: [1] },
    nodes: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          id: { type: 'string', required: true },
          kind: { type: 'string', required: true, enum: ['context', 'premise', 'inference', 'action', 'evidence', 'conclusion', 'failure'] },
          text: { type: 'string', required: true },
          source: { type: 'string', required: true, enum: ['agent_input', 'task_input', 'tool_trace', 'verifier_input', 'outcome_input', 'derived'] },
        },
      },
    },
    edges: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          sourceId: { type: 'string', required: true },
          targetId: { type: 'string', required: true },
          relation: { type: 'string', required: true, enum: ['supports', 'requires', 'leads_to', 'precedes', 'frames', 'guides', 'explains', 'qualifies', 'contradicts'] },
          source: { type: 'string', required: true, enum: ['agent_input', 'task_input', 'tool_trace', 'verifier_input', 'outcome_input', 'derived'] },
        },
      },
    },
  },
} as const

export const MEMORY_VIEW_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    memoryId: { type: 'string', required: true },
    type: { type: 'string', required: true, enum: ['strategy', 'structure', 'failure'] },
    outcome: { type: 'string', required: true, enum: ['success', 'fail'] },
    taskScope: { type: 'string', required: true },
    preconditions: { type: 'array', required: true, items: { type: 'string' } },
    title: { type: 'string', required: true },
    description: { type: 'string', required: true },
    contraindications: { type: 'array', required: true, items: { type: 'string' } },
    content: { type: 'string', required: true },
    reasoning: { type: 'string', required: true },
    reasoningGraph: REASONING_GRAPH_SCHEMA,
    verifier: { type: 'string', required: true },
    evidence: { type: 'string', required: true },
    trustLevel: { type: 'string', required: true, enum: ['verified', 'unverified', 'unknown'] },
    confidence: { type: 'number', required: true },
    feedback: {
      type: 'object', additionalProperties: false, required: true,
      properties: {
        helpful: { type: 'integer', required: true },
        harmful: { type: 'integer', required: true },
        neutral: { type: 'integer', required: true },
      },
    },
    structureSignature: { type: 'array', required: true, items: { type: 'string' } },
    semanticScore: { type: 'number', required: true },
    contextScore: { type: 'number', required: true },
    reasoningScore: { type: 'number', required: true },
    structureScore: { type: 'number', required: true },
    graphScore: { type: 'number', required: true },
    lexicalScore: { type: 'number', required: true },
    vectorScore: { type: 'number', required: true },
    templateScore: { type: 'number', required: true },
    deprecated: { type: 'boolean', required: true },
    combinedScore: { type: 'number', required: true },
    utilityMultiplier: { type: 'number', required: true },
    sourceSessionId: { type: 'string', required: true },
    sourceEventSeqs: { type: 'array', required: true, items: { type: 'integer' } },
  },
} as const

export const BEGIN_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    started: { type: 'boolean', required: true },
    taskId: { type: 'string', required: true },
    captureStatus: { type: 'string', required: true },
  },
} as const

export const ENTERPRISE_RULE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    ruleId: { type: 'string', required: true },
    ruleSetId: { type: 'string', required: true },
    title: { type: 'string', required: true },
    text: { type: 'string', required: true },
    source: { type: 'string', required: true },
    version: { type: 'string', required: true },
    priority: { type: 'number', required: true },
    validFrom: { type: 'number', required: true },
    validUntil: { type: 'number', required: true },
    score: { type: 'number', required: true },
  },
} as const

export const RETRIEVE_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    memories: { type: 'array', required: true, items: MEMORY_VIEW_SCHEMA },
    superposition: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          id: { type: 'string', required: true },
          amplitude: { type: 'number', required: true },
          phase: { type: 'number', required: true },
          p: { type: 'number', required: true },
          interferenceDeltaRaw: { type: 'number', required: true },
          interferenceDelta: { type: 'number', required: true },
          coherence: { type: 'number', required: true },
          collapseReason: { type: 'string', required: true },
        },
      },
    },
    route: { type: 'string', required: true, enum: ['auto', 'semantic', 'structure', 'graph', 'hybrid'] },
    fusionMode: { type: 'string', required: true, enum: ['weighted', 'rrf'] },
    enterpriseRules: { type: 'array', required: true, items: ENTERPRISE_RULE_SCHEMA },
    enterpriseRuleRagStatus: { type: 'string', required: true, enum: ['disabled', 'ready', 'no_matches', 'unavailable', 'invalid_configuration'] },
    enterpriseRuleRagRequired: { type: 'boolean', required: true },
    queryHash: { type: 'string', required: true },
    note: { type: 'string', required: true },
    balance: {
      type: 'object',
      additionalProperties: false,
      required: true,
      properties: {
        positiveCount: { type: 'integer', required: true },
        negativeCount: { type: 'integer', required: true },
        positiveNegativeRatio: { type: 'number', required: true },
        fallbackUsed: { type: 'boolean', required: true },
        thresholdFilteredCount: { type: 'integer', required: true },
      },
    },
  },
} as const

export const SAVE_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    stored: { type: 'boolean', required: true },
    reason: { type: 'string', required: true },
    memoryIds: { type: 'array', required: true, items: { type: 'string' } },
    trustLevel: { type: 'string', required: true, enum: ['unverified', 'not-applicable'] },
    gateRejectedCount: { type: 'integer', required: true },
    distillation: {
      type: 'object', required: true, additionalProperties: false,
      properties: {
        method: { type: 'string', required: true, enum: ['caller_insight', 'llm_distillation', 'llm_self_contrast', 'not-applicable'] },
        model: { type: 'string', required: true },
      },
    },
  },
} as const

export const CONSOLIDATE_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    stored: { type: 'boolean', required: true },
    reason: { type: 'string', required: true },
    memoryIds: { type: 'array', required: true, items: { type: 'string' } },
    trustLevel: { type: 'string', required: true, enum: ['unverified', 'not-applicable'] },
  },
} as const

export const MEMORY_QUERY_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    count: { type: 'integer', required: true },
    items: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          memoryId: { type: 'string', required: true },
          type: { type: 'string', required: true },
          outcome: { type: 'string', required: true },
          taskScope: { type: 'string', required: true },
          title: { type: 'string', required: true },
          description: { type: 'string', required: true },
          content: { type: 'string', required: true },
          reasoning: { type: 'string', required: true },
          preconditions: { type: 'array', required: true, items: { type: 'string' } },
          contraindications: { type: 'array', required: true, items: { type: 'string' } },
          structureSignature: { type: 'array', required: true, items: { type: 'string' } },
          trustLevel: { type: 'string', required: true },
          confidence: { type: 'number', required: true },
          feedback: {
            type: 'object', required: true, additionalProperties: false,
            properties: {
              helpful: { type: 'integer', required: true },
              harmful: { type: 'integer', required: true },
              neutral: { type: 'integer', required: true },
            },
          },
          provenance: {
            type: 'object', required: true, additionalProperties: false,
            properties: {
              taskId: { type: 'string', required: true },
              sourceSessionId: { type: 'string', required: true },
              eventSeqs: { type: 'array', required: true, items: { type: 'integer' } },
            },
          },
          evidence: {
            type: 'object', required: true, additionalProperties: false,
            properties: {
              verifier: { type: 'string', required: true },
              summary: { type: 'string', required: true },
              independentlyVerified: { type: 'boolean', required: true },
            },
          },
          createdAt: { type: 'number', required: true },
          updatedAt: { type: 'number', required: true },
          semanticText: { type: 'string', required: true },
          extractionMethod: { type: 'string', required: true },
          distillationModel: { type: 'string', required: true },
          sourceMemoryIds: { type: 'array', required: true, items: { type: 'string' } },
          strategyTemplateId: { type: 'string', required: true },
          reasoningLevel: { type: 'string', required: true },
          insightConfidence: { type: 'number', required: true },
          status: { type: 'string', required: true },
          causal: {
            type: 'object', required: true, additionalProperties: false,
            properties: {
              applied: { type: 'boolean', required: true },
              ate: { type: 'number', required: true },
              nEff: { type: 'integer', required: true },
              simpsonParadox: { type: 'boolean', required: true },
              estimatorVersion: { type: 'string', required: true },
            },
          },
        },
      },
    },
    note: { type: 'string', required: true },
  },
} as const

export const FEEDBACK_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    updated: { type: 'integer', required: true },
    skipped: { type: 'integer', required: true },
    templateUpdated: { type: 'integer', required: true },
    note: { type: 'string', required: true },
  },
} as const

export const PLAN_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    pattern: { type: 'string', required: true, enum: ['multi_hop_qa', 'code_synthesis', 'tool_orchestration', 'gui_operation', 'long_horizon_planning', 'numeric_search', 'general'] },
    confidence: { type: 'number', required: true },
    evidence: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          signal: { type: 'string', required: true },
          hit: { type: 'json', required: true },
          weight: { type: 'number', required: true },
        },
      },
    },
    scores: { type: 'json', required: true },
    fallback: { type: 'boolean', required: true },
    matchedTemplates: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          templateId: { type: 'string', required: true },
          name: { type: 'string', required: true },
          description: { type: 'string', required: true },
          taskPatterns: { type: 'array', required: true, items: { type: 'string' } },
          steps: {
            type: 'array', required: true,
            items: {
              type: 'object', additionalProperties: false,
              properties: {
                index: { type: 'integer', required: true },
                title: { type: 'string', required: true },
                moduleDemands: {
                  type: 'array', required: true,
                  items: {
                    type: 'object', additionalProperties: false,
                    properties: {
                      kind: { type: 'string', required: true, enum: ['retrieve_memory', 'retrieve_template', 'use_skill', 'invoke_tool', 'verify', 'rollback_check', 'retrieve_document'] },
                      intent: { type: 'string', required: true },
                    },
                  },
                },
              },
            },
          },
          structureSignature: { type: 'array', required: true, items: { type: 'string' } },
          activeRetrievalPoints: { type: 'array', required: true, items: { type: 'integer' } },
          score: { type: 'number', required: true },
          breakdown: {
            type: 'object', required: true, additionalProperties: false,
            properties: {
              struct: { type: 'number', required: true },
              lexical: { type: 'number', required: true },
              pattern: { type: 'number', required: true },
              quality: { type: 'number', required: true },
            },
          },
          strongMatch: { type: 'boolean', required: true },
          injectable: { type: 'boolean', required: true },
          deprecated: { type: 'boolean', required: true },
          utility: {
            type: 'object', required: true, additionalProperties: false,
            properties: {
              helpful: { type: 'integer', required: true },
              harmful: { type: 'integer', required: true },
              neutral: { type: 'integer', required: true },
            },
          },
        },
      },
    },
    recommendedSkills: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          name: { type: 'string', required: true },
          description: { type: 'string', required: true },
        },
      },
    },
    superposition: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          id: { type: 'string', required: true },
          amplitude: { type: 'number', required: true },
          phase: { type: 'number', required: true },
          p: { type: 'number', required: true },
          interferenceDeltaRaw: { type: 'number', required: true },
          interferenceDelta: { type: 'number', required: true },
          coherence: { type: 'number', required: true },
          collapseReason: { type: 'string', required: true },
        },
      },
    },
    plan: { type: 'string', required: true },
    audit: { type: 'json', required: true },
    note: { type: 'string', required: true },
  },
} as const

export const TEMPLATE_LIST_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    count: { type: 'integer', required: true },
    templates: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          templateId: { type: 'string', required: true },
          name: { type: 'string', required: true },
          description: { type: 'string', required: true },
          taskPatterns: { type: 'array', required: true, items: { type: 'string' } },
          version: { type: 'integer', required: true },
          status: { type: 'string', required: true, enum: ['active', 'deprecated'] },
          utility: {
            type: 'object', required: true, additionalProperties: false,
            properties: {
              helpful: { type: 'integer', required: true },
              harmful: { type: 'integer', required: true },
              neutral: { type: 'integer', required: true },
            },
          },
        },
      },
    },
    note: { type: 'string', required: true },
  },
} as const

export const TEMPLATE_LOAD_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    found: { type: 'boolean', required: true },
    templateId: { type: 'string', required: true },
    name: { type: 'string', required: true },
    description: { type: 'string', required: true },
    taskPatterns: { type: 'array', required: true, items: { type: 'string' } },
    steps: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          index: { type: 'integer', required: true },
          title: { type: 'string', required: true },
          moduleDemands: {
            type: 'array', required: true,
            items: {
              type: 'object', additionalProperties: false,
              properties: {
                kind: { type: 'string', required: true, enum: ['retrieve_memory', 'retrieve_template', 'use_skill', 'invoke_tool', 'verify', 'rollback_check', 'retrieve_document'] },
                intent: { type: 'string', required: true },
              },
            },
          },
        },
      },
    },
    structureSignature: { type: 'array', required: true, items: { type: 'string' } },
    preconditions: { type: 'array', required: true, items: { type: 'string' } },
    provenance: {
      type: 'object', required: true, additionalProperties: false,
      properties: {
        author: { type: 'string', required: true, enum: ['builtin', 'mined', 'caller'] },
        sourceMemoryIds: { type: 'array', required: true, items: { type: 'string' } },
        sourceTaskIds: { type: 'array', required: true, items: { type: 'string' } },
      },
    },
    utility: {
      type: 'object', required: true, additionalProperties: false,
      properties: {
        helpful: { type: 'integer', required: true },
        harmful: { type: 'integer', required: true },
        neutral: { type: 'integer', required: true },
      },
    },
    version: { type: 'integer', required: true },
    status: { type: 'string', required: true, enum: ['active', 'deprecated'] },
    note: { type: 'string', required: true },
  },
} as const

export const TEMPLATE_PUBLISH_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    published: { type: 'boolean', required: true },
    templateId: { type: 'string', required: true },
    name: { type: 'string', required: true },
    version: { type: 'integer', required: true },
    structureSignature: { type: 'array', required: true, items: { type: 'string' } },
    note: { type: 'string', required: true },
  },
} as const

export const DOCUMENT_IMPORT_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    imported: { type: 'boolean', required: true },
    docId: { type: 'string', required: true },
    name: { type: 'string', required: true },
    version: { type: 'string', required: true },
    chunkCount: { type: 'integer', required: true },
    charCount: { type: 'integer', required: true },
    note: { type: 'string', required: true },
  },
} as const

export const DOCUMENT_LIST_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    count: { type: 'integer', required: true },
    documents: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          docId: { type: 'string', required: true },
          name: { type: 'string', required: true },
          source: { type: 'string', required: true },
          version: { type: 'string', required: true },
          taskPatterns: { type: 'array', required: true, items: { type: 'string' } },
          chunkCount: { type: 'integer', required: true },
          charCount: { type: 'integer', required: true },
          importedAt: { type: 'number', required: true },
        },
      },
    },
    note: { type: 'string', required: true },
  },
} as const

export const DOCUMENT_LOAD_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    found: { type: 'boolean', required: true },
    docId: { type: 'string', required: true },
    name: { type: 'string', required: true },
    source: { type: 'string', required: true },
    version: { type: 'string', required: true },
    taskPatterns: { type: 'array', required: true, items: { type: 'string' } },
    chunkCount: { type: 'integer', required: true },
    chunks: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          chunkId: { type: 'string', required: true },
          seq: { type: 'integer', required: true },
          headingPath: { type: 'array', required: true, items: { type: 'string' } },
          text: { type: 'string', required: true },
        },
      },
    },
    note: { type: 'string', required: true },
  },
} as const

export const DOCUMENT_RETRIEVE_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    documents: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          chunkId: { type: 'string', required: true },
          docId: { type: 'string', required: true },
          docName: { type: 'string', required: true },
          source: { type: 'string', required: true },
          version: { type: 'string', required: true },
          headingPath: { type: 'array', required: true, items: { type: 'string' } },
          text: { type: 'string', required: true },
          seq: { type: 'integer', required: true },
          hop: { type: 'integer', required: true },
          viaChunkId: { type: 'string', required: true },
          score: { type: 'number', required: true },
          breakdown: {
            type: 'object', required: true, additionalProperties: false,
            properties: {
              lexical: { type: 'number', required: true },
              vector: { type: 'number', required: true },
              pattern: { type: 'number', required: true },
              link: { type: 'number', required: true },
            },
          },
        },
      },
    },
    pattern: { type: 'string', required: true, enum: ['multi_hop_qa', 'code_synthesis', 'tool_orchestration', 'gui_operation', 'long_horizon_planning', 'numeric_search', 'general'] },
    patternFallback: { type: 'boolean', required: true },
    fusionMode: { type: 'string', required: true, enum: ['rrf', 'weighted'] },
    audit: { type: 'json', required: true },
    note: { type: 'string', required: true },
  },
} as const

export const DOCUMENT_DELETE_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    deleted: { type: 'boolean', required: true },
    docId: { type: 'string', required: true },
    removedChunks: { type: 'integer', required: true },
    note: { type: 'string', required: true },
  },
} as const

export const API_STATUS_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    enabled: { type: 'boolean', required: true },
    path: { type: 'string', required: true },
    loaded: { type: 'boolean', required: true },
    lastError: { type: 'string', required: true },
    proxy: {
      type: 'object', required: true, additionalProperties: false,
      properties: {
        url: { type: 'string', required: true },
        noProxy: { type: 'array', required: true, items: { type: 'string' } },
      },
    },
    endpoints: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          name: { type: 'string', required: true },
          kind: { type: 'string', required: true, enum: ['openai-embeddings', 'openai-chat', 'http-json'] },
          baseUrl: { type: 'string', required: true },
          model: { type: 'string' },
          dimension: { type: 'number' },
          temperature: { type: 'number' },
          keySource: { type: 'string', required: true, enum: ['env', 'inline', 'none'] },
          proxy: { type: 'string', required: true, enum: ['inherit', 'direct', 'custom'] },
        },
      },
    },
    note: { type: 'string', required: true },
  },
} as const

export const SKILL_PUBLISH_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    published: { type: 'boolean', required: true },
    skillId: { type: 'string', required: true },
    name: { type: 'string', required: true },
    version: { type: 'integer', required: true },
    resourceCount: { type: 'integer', required: true },
    note: { type: 'string', required: true },
  },
} as const

export const SKILL_LIST_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    count: { type: 'integer', required: true },
    skills: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          name: { type: 'string', required: true },
          description: { type: 'string', required: true },
          version: { type: 'integer', required: true },
          resourceCount: { type: 'integer', required: true },
          sourceMemoryCount: { type: 'integer', required: true },
          updatedAt: { type: 'number', required: true },
          trustLevel: { type: 'string', required: true, enum: ['unverified'] },
        },
      },
    },
    note: { type: 'string', required: true },
  },
} as const

export const SKILL_LOAD_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    found: { type: 'boolean', required: true },
    name: { type: 'string', required: true },
    description: { type: 'string', required: true },
    body: { type: 'string', required: true },
    version: { type: 'integer', required: true },
    resources: { type: 'array', required: true, items: { type: 'string' } },
    sourceMemoryIds: { type: 'array', required: true, items: { type: 'string' } },
    trustLevel: { type: 'string', required: true, enum: ['unverified'] },
    note: { type: 'string', required: true },
  },
} as const

export const SKILL_RESOURCE_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    found: { type: 'boolean', required: true },
    path: { type: 'string', required: true },
    content: { type: 'string', required: true },
    note: { type: 'string', required: true },
  },
} as const

export const END_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    ended: { type: 'boolean', required: true },
    taskId: { type: 'string', required: true },
    observedEvents: { type: 'integer', required: true },
    captureTruncated: { type: 'boolean', required: true },
    reasoningCaptureStatus: { type: 'string', required: true, enum: ['disabled', 'provided', 'not-provided'] },
    usage: {
      type: 'object', required: true, additionalProperties: false,
      properties: {
        inputTokens: { type: 'number', required: true },
        outputTokens: { type: 'number', required: true },
        reasoningTokens: { type: 'number', required: true },
      },
    },
  },
} as const
