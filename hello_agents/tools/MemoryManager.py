class MemoryManager:
    """记忆管理器 - 统一的记忆操作接口"""

    def __init__(
          self,
          config: Optional[MemoryConfig] = None,
          user_id: str = "default_user",
          enable_working: bool = True,
          enable_episodic: bool = True,
          enable_semantic: bool = True,
          enable_perceptual: bool = False
      ):
        self.config = config or MemoryConfig()
        self.user_id = user_id

        # 初始化存储和检索组件
        self.store = MemoryStore(self.config)
        self.retriever = MemoryRetriever(self.store, self.config)

        # 初始化各类型记忆
        self.memory_types = {}

        if enable_working:
            self.memory_types['working'] = WorkingMemory(self.config, self.store)

        if enable_episodic:
            self.memory_types['episodic'] = EpisodicMemory(self.config, self.store)

        if enable_semantic:
            self.memory_types['semantic'] = SemanticMemory(self.config, self.store)

        if enable_perceptual:
            self.memory_types['perceptual'] = PerceptualMemory(self.config, self.store)


class EpisodicMemory:
    """情景记忆实现
    特点：
    - SQLite+Qdrant混合存储架构
    - 支持时间序列和会话级检索
    - 结构化过滤 + 语义向量检索
    """
    
    def __init__(self, config: MemoryConfig):
        self.doc_store = SQLiteDocumentStore(config.database_path)
        self.vector_store = QdrantVectorStore(config.qdrant_url, config.qdrant_api_key)
        self.embedder = create_embedding_model_with_fallback()
        self.sessions = {}  # 会话索引
    
    def add(self, memory_item: MemoryItem) -> str:
        """添加情景记忆"""
        # 创建情景对象
        episode = Episode(
            episode_id=memory_item.id,
            session_id=memory_item.metadata.get("session_id", "default"),
            timestamp=memory_item.timestamp,
            content=memory_item.content,
            context=memory_item.metadata
        )
        
        # 更新会话索引
        session_id = episode.session_id
        if session_id not in self.sessions:
            self.sessions[session_id] = []
        self.sessions[session_id].append(episode.episode_id)
        
        # 持久化存储（SQLite + Qdrant）
        self._persist_episode(episode)
        return memory_item.id
    
    def retrieve(self, query: str, limit: int = 5, **kwargs) -> List[MemoryItem]:
        """混合检索：结构化过滤 + 语义向量检索"""
        # 1. 结构化预过滤（时间范围、重要性等）
        candidate_ids = self._structured_filter(**kwargs)
        
        # 2. 向量语义检索
        hits = self._vector_search(query, limit * 5, kwargs.get("user_id"))
        
        # 3. 综合评分与排序
        results = []
        for hit in hits:
            if self._should_include(hit, candidate_ids, kwargs):
                score = self._calculate_episode_score(hit)
                memory_item = self._create_memory_item(hit)
                results.append((score, memory_item))
        
        results.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in results[:limit]]
    
    def _calculate_episode_score(self, hit) -> float:
        """情景记忆评分算法"""
        vec_score = float(hit.get("score", 0.0))
        recency_score = self._calculate_recency(hit["metadata"]["timestamp"])
        importance = hit["metadata"].get("importance", 0.5)
        
        # 评分公式：(向量相似度 × 0.8 + 时间近因性 × 0.2) × 重要性权重
        base_relevance = vec_score * 0.8 + recency_score * 0.2
        importance_weight = 0.8 + (importance * 0.4)
        
        return base_relevance * importance_weight



class SemanticMemory(BaseMemory):
    """语义记忆实现
    
    特点：
    - 使用HuggingFace中文预训练模型进行文本嵌入
    - 向量检索进行快速相似度匹配
    - 知识图谱存储实体和关系
    - 混合检索策略：向量+图+语义推理
    """
    
    def __init__(self, config: MemoryConfig, storage_backend=None):
        super().__init__(config, storage_backend)
        
        # 嵌入模型（统一提供）
        self.embedding_model = get_text_embedder()
        
        # 专业数据库存储
        self.vector_store = QdrantConnectionManager.get_instance(**qdrant_config)
        self.graph_store = Neo4jGraphStore(**neo4j_config)
        
        # 实体和关系缓存
        self.entities: Dict[str, Entity] = {}
        self.relations: List[Relation] = []
        
        # NLP处理器（支持中英文）
        self.nlp = self._init_nlp()
    
    def add(self, memory_item: MemoryItem) -> str:
      """添加语义记忆"""
      # 1. 生成文本嵌入
      embedding = self.embedding_model.encode(memory_item.content)
      
      # 2. 提取实体和关系
      entities = self._extract_entities(memory_item.content)
      relations = self._extract_relations(memory_item.content, entities)
      
      # 3. 存储到Neo4j图数据库
      for entity in entities:
          self._add_entity_to_graph(entity, memory_item)
      
      for relation in relations:
          self._add_relation_to_graph(relation, memory_item)
      
      # 4. 存储到Qdrant向量数据库
      metadata = {
          "memory_id": memory_item.id,
          "entities": [e.entity_id for e in entities],
          "entity_count": len(entities),
          "relation_count": len(relations)
      }
      
      self.vector_store.add_vectors(
          vectors=[embedding.tolist()],
          metadata=[metadata],
          ids=[memory_item.id]
      )

    def retrieve(self, query: str, limit: int = 5, **kwargs) -> List[MemoryItem]:
      """检索语义记忆"""
      # 1. 向量检索
      vector_results = self._vector_search(query, limit * 2, user_id)
      
      # 2. 图检索
      graph_results = self._graph_search(query, limit * 2, user_id)
      
      # 3. 混合排序
      combined_results = self._combine_and_rank_results(
          vector_results, graph_results, query, limit
      )
      
      return combined_results[:limit]


    def _combine_and_rank_results(self, vector_results, graph_results, query, limit):
      """混合排序结果"""
      combined = {}
      
      # 合并向量和图检索结果
      for result in vector_results:
          combined[result["memory_id"]] = {
              **result,
              "vector_score": result.get("score", 0.0),
              "graph_score": 0.0
          }
      
      for result in graph_results:
          memory_id = result["memory_id"]
          if memory_id in combined:
              combined[memory_id]["graph_score"] = result.get("similarity", 0.0)
          else:
              combined[memory_id] = {
                  **result,
                  "vector_score": 0.0,
                  "graph_score": result.get("similarity", 0.0)
              }
      
      # 计算混合分数
      for memory_id, result in combined.items():
          vector_score = result["vector_score"]
          graph_score = result["graph_score"]
          importance = result.get("importance", 0.5)
          
          # 基础相似度得分
          base_relevance = vector_score * 0.7 + graph_score * 0.3
          
          # 重要性权重 [0.8, 1.2]
          importance_weight = 0.8 + (importance * 0.4)
          
          # 最终得分：相似度 * 重要性权重
          combined_score = base_relevance * importance_weight
          result["combined_score"] = combined_score
      
      # 排序并返回
      sorted_results = sorted(
          combined.values(),
          key=lambda x: x["combined_score"],
          reverse=True
      )
      
      return sorted_results[:limit]


class PerceptualMemory(BaseMemory):
    """感知记忆实现
    
    特点：
    - 支持多模态数据（文本、图像、音频等）
    - 跨模态相似性搜索
    - 感知数据的语义理解
    - 支持内容生成和检索
    """
    
    def __init__(self, config: MemoryConfig, storage_backend=None):
        super().__init__(config, storage_backend)
        
        # 多模态编码器
        self.text_embedder = get_text_embedder()
        self._clip_model = self._init_clip_model()  # 图像编码
        self._clap_model = self._init_clap_model()  # 音频编码
        
        # 按模态分离的向量存储
        self.vector_stores = {
            "text": QdrantConnectionManager.get_instance(
                collection_name="perceptual_text",
                vector_size=self.vector_dim
            ),
            "image": QdrantConnectionManager.get_instance(
                collection_name="perceptual_image", 
                vector_size=self._image_dim
            ),
            "audio": QdrantConnectionManager.get_instance(
                collection_name="perceptual_audio",
                vector_size=self._audio_dim
            )
        }
    
    def retrieve(self, query: str, limit: int = 5, **kwargs) -> List[MemoryItem]:
      """检索感知记忆（可筛模态；同模态向量检索+时间/重要性融合）"""
      user_id = kwargs.get("user_id")
      target_modality = kwargs.get("target_modality")
      query_modality = kwargs.get("query_modality", target_modality or "text")
      
      # 同模态向量检索
      try:
          query_vector = self._encode_data(query, query_modality)
          store = self._get_vector_store_for_modality(target_modality or query_modality)
          
          where = {"memory_type": "perceptual"}
          if user_id:
              where["user_id"] = user_id
          if target_modality:
              where["modality"] = target_modality
          
          hits = store.search_similar(
              query_vector=query_vector,
              limit=max(limit * 5, 20),
              where=where
          )
      except Exception:
          hits = []
      
      # 融合排序（向量相似度 + 时间近因性 + 重要性权重）
      results = []
      for hit in hits:
          vector_score = float(hit.get("score", 0.0))
          recency_score = self._calculate_recency_score(hit["metadata"]["timestamp"])
          importance = hit["metadata"].get("importance", 0.5)
          
          # 评分算法
          base_relevance = vector_score * 0.8 + recency_score * 0.2
          importance_weight = 0.8 + (importance * 0.4)
          combined_score = base_relevance * importance_weight
          
          results.append((combined_score, self._create_memory_item(hit)))
      
      results.sort(key=lambda x: x[0], reverse=True)
      return [item for _, item in results[:limit]]

    def _calculate_recency_score(self, timestamp: str) -> float:
      """计算时间近因性得分"""
      try:
          memory_time = datetime.fromisoformat(timestamp)
          current_time = datetime.now()
          age_hours = (current_time - memory_time).total_seconds() / 3600
          
          # 指数衰减：24小时内保持高分，之后逐渐衰减
          decay_factor = 0.1  # 衰减系数
          recency_score = math.exp(-decay_factor * age_hours / 24)
          
          return max(0.1, recency_score)  # 最低保持0.1的基础分数
      except Exception:
          return 0.5  # 默认中等分数