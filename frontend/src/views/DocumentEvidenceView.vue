<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, type CSSProperties } from 'vue'
import { useRoute } from 'vue-router'
import {
  request,
  type DocumentJob,
  type EvidenceReviewResponse,
  type OutputResponse,
  type PageResponse,
} from '../api/client'
import { bboxStyle } from '../utils/evidence'

const route = useRoute()
const documentId = String(route.params.id)

const outputs = ref<OutputResponse[]>([])
const jobs = ref<DocumentJob[]>([])
const reviews = ref<EvidenceReviewResponse[]>([])
const selectedPageNumber = ref(1)
const selectedBlockId = ref<string | null>(null)
const selectedSealId = ref<string | null>(null)
const pageImageUrl = ref('')
const displaySize = ref({ width: 0, height: 0 })
const sealStatus = ref('present')
const sealReason = ref('')
const signatureStatus = ref('present')
const signatureReason = ref('')
const rerunReason = ref('')
const actionError = ref('')
let timer: number | undefined

const latestOutput = computed(
  () => [...outputs.value].sort((a, b) => b.version - a.version)[0] ?? null,
)
const selectedPage = computed(
  () => latestOutput.value?.pages.find((page) => page.number === selectedPageNumber.value) ?? null,
)
const latestJob = computed(() => jobs.value.at(-1))
const selectedSeal = computed(
  () => selectedPage.value?.seals.find((seal) => seal.id === selectedSealId.value) ?? null,
)

const statusLabels: Record<string, string> = {
  success: '成功',
  partial_success: '部分成功',
  failed: '失败',
  waiting: '等待处理',
  running: '处理中',
  not_applicable: '不适用',
  manual_handling: '需人工处理',
}
const stepLabels: Record<string, string> = {
  validation: '材料验证',
  parsing_ocr: '解析/OCR',
  structure_extraction: '结构抽取',
  seal_detection: '印章候选检测',
  classification: '分类',
  candidate_extraction: '候选抽取',
}
const pageErrorLabels: Record<string, string> = {
  page_analysis_failed: '该页分析失败，其他页仍可复核',
}
const stepErrorLabels: Record<string, string> = {
  partial_page_failure: '部分页面解析失败',
  all_pages_failed: '全部页面解析失败',
}

function itemStyle(
  item: { bbox: [number, number, number, number] },
  page: PageResponse,
): CSSProperties {
  const width = displaySize.value.width || page.width * 2
  const height = displaySize.value.height || page.height * 2
  return bboxStyle(item.bbox, page, width, height) as CSSProperties
}

async function refresh() {
  outputs.value = await request<OutputResponse[]>(`/api/v1/documents/${documentId}/outputs`)
  jobs.value = await request<DocumentJob[]>(`/api/v1/documents/${documentId}/jobs`)
  if (latestOutput.value) {
    reviews.value = await request<EvidenceReviewResponse[]>(
      `/api/v1/document-outputs/${latestOutput.value.id}/reviews`,
    )
  } else {
    reviews.value = []
  }
  if (pageImageUrl.value) return
  await loadPageImage()
}

async function loadPageImage() {
  const response = await fetch(
    `/api/v1/documents/${documentId}/pages/${selectedPageNumber.value}/image`,
    { credentials: 'same-origin' },
  )
  if (!response.ok) {
    actionError.value = response.status === 404 ? '原页预览暂不可用' : '原页预览加载失败'
    pageImageUrl.value = ''
    return
  }
  if (pageImageUrl.value) URL.revokeObjectURL(pageImageUrl.value)
  pageImageUrl.value = URL.createObjectURL(await response.blob())
}

async function selectPage(number: number) {
  selectedPageNumber.value = number
  selectedBlockId.value = null
  selectedSealId.value = null
  await loadPageImage()
}

function selectBlock(id: string) {
  selectedBlockId.value = id
  selectedSealId.value = null
}

function selectSeal(id: string) {
  selectedSealId.value = id
  selectedBlockId.value = null
}

async function submitReview(kind: 'seal_presence' | 'signature_presence') {
  if (!latestOutput.value) return
  actionError.value = ''
  const payload = {
    kind,
    status: kind === 'seal_presence' ? sealStatus.value : signatureStatus.value,
    seal_candidate_id: kind === 'seal_presence' ? (selectedSeal.value?.id ?? null) : null,
    reason: kind === 'seal_presence' ? sealReason.value : signatureReason.value,
  }
  try {
    const review = await request<EvidenceReviewResponse>(
      `/api/v1/document-outputs/${latestOutput.value.id}/reviews`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify(payload),
      },
    )
    reviews.value.push(review)
    if (kind === 'seal_presence') sealReason.value = ''
    else signatureReason.value = ''
  } catch (caught) {
    actionError.value = caught instanceof Error ? caught.message : '提交失败'
  }
}

async function rerunParser() {
  if (!latestJob.value) return
  const reason = rerunReason.value.trim()
  if (!reason) return
  actionError.value = ''
  try {
    await request<DocumentJob>(`/api/v1/jobs/${latestJob.value.id}/retry`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({
        reason,
        selected_steps: ['parsing_ocr', 'seal_detection'],
      }),
    })
    rerunReason.value = ''
    await refresh()
  } catch (caught) {
    actionError.value = caught instanceof Error ? caught.message : '重试失败'
  }
}

onMounted(async () => {
  await refresh()
  timer = window.setInterval(refresh, 3000)
})
onUnmounted(() => {
  window.clearInterval(timer)
  if (pageImageUrl.value) URL.revokeObjectURL(pageImageUrl.value)
})
</script>

<template>
  <section>
    <router-link to="/applications">返回申请列表</router-link>
    <h1>证据预览与人工确认</h1>

    <p v-if="!latestOutput" role="status">
      尚无解析输出。若材料仍在处理中，请稍候，页面会自动刷新。
    </p>

    <template v-if="latestOutput">
      <el-alert
        v-if="latestOutput.status !== 'success'"
        type="warning"
        :closable="false"
        :title="`本版本解析${statusLabels[latestOutput.status] || latestOutput.status}，部分页面可能不可复核`"
      />
      <el-descriptions border :column="3">
        <el-descriptions-item label="输出版本">
          v{{ latestOutput.version }}
        </el-descriptions-item>
        <el-descriptions-item label="解析器版本">
          {{ latestOutput.parser_version }}
        </el-descriptions-item>
        <el-descriptions-item label="模型版本">
          {{ latestOutput.model_version }}
        </el-descriptions-item>
      </el-descriptions>

      <h2>页面与解析结果</h2>
      <el-select
        :model-value="selectedPageNumber"
        :aria-label="'选择页面'"
        @update:model-value="selectPage"
      >
        <el-option
          v-for="page in latestOutput.pages"
          :key="page.number"
          :value="page.number"
          :label="`第 ${page.number} 页（${statusLabels[page.status] || page.status}）`"
        />
      </el-select>
      <p v-if="selectedPage?.error_code" role="alert">
        {{ pageErrorLabels[selectedPage.error_code] || selectedPage.error_code }}
      </p>

      <template v-if="selectedPage">
        <h2>原页预览（选中文字或印章候选可高亮区域）</h2>
        <div class="preview-scroll">
          <div
            class="page-preview"
            :style="{
              width: `${displaySize.width || selectedPage.width * 2}px`,
              height: `${displaySize.height || selectedPage.height * 2}px`,
            }"
          >
            <img
              v-if="pageImageUrl"
              :src="pageImageUrl"
              :alt="`第 ${selectedPage.number} 页原页`"
              @load="(event) => {
                const image = event.target as HTMLImageElement
                displaySize = { width: image.naturalWidth, height: image.naturalHeight }
              }"
            >
            <button
              v-for="block in selectedPage.blocks"
              :key="block.id"
              class="evidence-box block-box"
              :class="{ selected: selectedBlockId === block.id }"
              :style="itemStyle(block, selectedPage)"
              :aria-label="`文字块-${block.text}`"
              @click="selectBlock(block.id)"
            />
            <button
              v-for="seal in selectedPage.seals"
              :key="seal.id"
              class="evidence-box seal-box"
              :class="{ selected: selectedSealId === seal.id }"
              :style="itemStyle(seal, selectedPage)"
              :aria-label="`印章候选-${seal.text}`"
              @click="selectSeal(seal.id)"
            />
          </div>
        </div>
        <p class="candidate-note">
          印章候选是系统从原页检测到的印章文字区域，仅供审批人员人工核对；系统不判断印章真实性、归属或法律效力。
        </p>
      </template>

      <h2>文字块</h2>
      <el-table :data="selectedPage?.blocks ?? []" empty-text="本页没有可复核文字块" height="220">
        <el-table-column label="文字" prop="text" />
        <el-table-column label="提取方式" width="120">
          <template #default="scope">
            {{ scope.row.extraction_method === 'pdf_text' ? 'PDF文本层' : 'OCR' }}
          </template>
        </el-table-column>
        <el-table-column label="置信度" width="100">
          <template #default="scope">
            {{ scope.row.confidence == null ? '—' : scope.row.confidence.toFixed(2) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="scope">
            <el-button
              size="small"
              :aria-label="`定位文字块-${scope.row.text}`"
              @click="selectBlock(scope.row.id)"
            >定位</el-button>
          </template>
        </el-table-column>
      </el-table>

      <h2>印章候选（未经人工确认）</h2>
      <el-table :data="selectedPage?.seals ?? []" empty-text="本页未检测到印章候选">
        <el-table-column label="候选文字" prop="text" />
        <el-table-column label="置信度" width="100">
          <template #default="scope">
            {{ scope.row.confidence.toFixed(2) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140">
          <template #default="scope">
            <el-button
              size="small"
              :aria-label="`确认印章候选-${scope.row.text}`"
              @click="selectSeal(scope.row.id)"
            >确认</el-button>
          </template>
        </el-table-column>
      </el-table>

      <h2>人工确认</h2>
      <el-card v-if="selectedSeal" class="review-card">
        <h3>确认印章候选“{{ selectedSeal.text }}”</h3>
        <p class="candidate-note">
          人工确认只记录审批人员对印章存在性的判断，不表示印章真实、有效或归属某方。
        </p>
        <el-radio-group v-model="sealStatus">
          <el-radio value="present">存在</el-radio>
          <el-radio value="absent">不存在</el-radio>
          <el-radio value="uncertain">不确定</el-radio>
        </el-radio-group>
        <el-input
          v-model="sealReason"
          :aria-label="'印章确认理由'"
          placeholder="填写确认理由（必填）"
        />
        <el-button
          :disabled="!sealReason.trim()"
          :aria-label="'提交印章确认'"
          @click="submitReview('seal_presence')"
        >提交印章确认</el-button>
      </el-card>
      <el-card class="review-card">
        <h3>人工确认签字存在性</h3>
        <p class="candidate-note">
          签字状态只能由审批人员人工确认，系统不提供自动签字检测，也不判断签字真实性。
        </p>
        <el-radio-group v-model="signatureStatus">
          <el-radio value="present">存在</el-radio>
          <el-radio value="absent">不存在</el-radio>
          <el-radio value="uncertain">不确定</el-radio>
        </el-radio-group>
        <el-input
          v-model="signatureReason"
          :aria-label="'签字确认理由'"
          placeholder="填写确认理由（必填）"
        />
        <el-button
          :disabled="!signatureReason.trim()"
          :aria-label="'提交签字确认'"
          @click="submitReview('signature_presence')"
        >提交签字确认</el-button>
      </el-card>
      <p v-if="actionError" role="alert">{{ actionError }}</p>

      <h2>已提交的确认记录</h2>
      <el-table :data="reviews" empty-text="尚无人工确认记录">
        <el-table-column label="类型" width="140">
          <template #default="scope">
            {{ scope.row.kind === 'seal_presence' ? '印章存在性' : '签字存在性' }}
          </template>
        </el-table-column>
        <el-table-column label="结论" width="100" prop="status" />
        <el-table-column label="理由" prop="reason" />
        <el-table-column label="时间" width="200" prop="created_at" />
      </el-table>

      <h2>处理步骤与重跑</h2>
      <el-table :data="latestJob?.steps ?? []" empty-text="暂无处理步骤">
        <el-table-column label="步骤" width="160">
          <template #default="scope">
            {{ stepLabels[scope.row.name] || scope.row.name }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="140">
          <template #default="scope">
            {{ statusLabels[scope.row.status] || scope.row.status }}
          </template>
        </el-table-column>
        <el-table-column label="错误" prop="error_code">
          <template #default="scope">
            {{ stepErrorLabels[scope.row.error_code] || scope.row.error_code || '—' }}
          </template>
        </el-table-column>
      </el-table>
      <template v-if="latestJob">
        <el-input
          v-model="rerunReason"
          :aria-label="'解析重跑原因'"
          placeholder="填写重新解析原因（必填）"
        />
        <el-button
          :disabled="!rerunReason.trim() || latestJob.status === 'waiting' || latestJob.status === 'running'"
          :aria-label="'重跑解析与印章检测'"
          @click="rerunParser"
        >用新模型重新解析（产生新输出版本）</el-button>
        <p v-if="latestJob.retry_reason" class="candidate-note">
          最近一次重跑原因：{{ latestJob.retry_reason }}
        </p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.preview-scroll {
  overflow: auto;
  border: 1px solid #dcdfe6;
  background: #f5f7fa;
  padding: 8px;
}
.page-preview {
  position: relative;
}
.page-preview img {
  display: block;
  width: 100%;
  height: 100%;
}
.evidence-box {
  position: absolute;
  border: 2px solid transparent;
  background: transparent;
  cursor: pointer;
  padding: 0;
}
.block-box {
  border-color: rgba(23, 105, 170, 0.6);
}
.block-box.selected {
  background: rgba(23, 105, 170, 0.25);
}
.seal-box {
  border-color: rgba(214, 69, 65, 0.75);
  border-style: dashed;
}
.seal-box.selected {
  background: rgba(214, 69, 65, 0.3);
}
.candidate-note {
  color: #606266;
  font-size: 12px;
}
.review-card {
  margin-bottom: 12px;
}
</style>
