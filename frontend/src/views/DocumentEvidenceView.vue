<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, type CSSProperties } from 'vue'
import { useRoute } from 'vue-router'
import {
  request,
  type BlockResponse,
  type CellResponse,
  type DocumentJob,
  type EvidenceReviewResponse,
  type OutputResponse,
  type PageResponse,
} from '../api/client'
import { bboxStyle } from '../utils/evidence'
import { blockLocation, cellLocation, tableGrid, type NativeLocator } from '../utils/structured'

const route = useRoute()
const documentId = String(route.params.id)

const outputs = ref<OutputResponse[]>([])
const jobs = ref<DocumentJob[]>([])
const reviews = ref<EvidenceReviewResponse[]>([])
const selectedPageNumber = ref(1)
const selectedBlockId = ref<string | null>(null)
const selectedSealId = ref<string | null>(null)
const selectedCellId = ref<string | null>(null)
const pageImageUrl = ref('')
const displaySize = ref({ width: 0, height: 0 })
const sealStatus = ref('present')
const sealReason = ref('')
const signatureStatus = ref('present')
const signatureReason = ref('')
const rerunReason = ref('')
const actionError = ref('')
const blockElements = new Map<string, HTMLElement>()
let timer: number | undefined

const latestOutput = computed(
  () => [...outputs.value].sort((a, b) => b.version - a.version)[0] ?? null,
)
const isStructured = computed(() =>
  ['docx', 'xlsx', 'csv', 'markdown'].includes(latestOutput.value?.format ?? ''),
)
const structuredBlocks = computed(() =>
  (latestOutput.value?.pages[0]?.blocks ?? []).map((block) => ({
    ...block,
    grid: tableGrid(block),
  })),
)
const selectedPage = computed(
  () => latestOutput.value?.pages.find((page) => page.number === selectedPageNumber.value) ?? null,
)
const latestJob = computed(() => jobs.value.at(-1))
const selectedSeal = computed(
  () => selectedPage.value?.seals.find((seal) => seal.id === selectedSealId.value) ?? null,
)
const selectedCell = computed(() => {
  for (const block of structuredBlocks.value) {
    const found = block.grid.flat().find((cell) => cell.id === selectedCellId.value)
    if (found) return found
  }
  return null
})
const rerunnableSteps = computed(() => {
  const job = latestJob.value
  if (!job) return []
  return job.steps
    .filter((step) => step.name === 'parsing_ocr' || step.name === 'seal_detection')
    .filter((step) => step.status !== 'not_applicable')
    .map((step) => step.name)
})
const downloadUrl = `/api/v1/documents/${documentId}/download`

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
  item: { bbox: [number, number, number, number] | null },
  page: PageResponse,
): CSSProperties {
  if (!item.bbox) return {}
  const width = displaySize.value.width || (page.width ?? 0) * 2
  const height = displaySize.value.height || (page.height ?? 0) * 2
  return bboxStyle(item.bbox, { width: page.width ?? 0, height: page.height ?? 0 }, width, height) as CSSProperties
}

function previewText(text: string): string {
  return text.length > 60 ? `${text.slice(0, 60)}…` : text
}

function setBlockRef(id: string, element: unknown) {
  if (element instanceof HTMLElement) blockElements.set(id, element)
}

function focusBlock(id: string) {
  selectedBlockId.value = id
  selectedCellId.value = null
  nextTick(() => blockElements.get(id)?.scrollIntoView?.({ behavior: 'smooth', block: 'center' }))
}

function selectCell(cell: CellResponse) {
  selectedCellId.value = cell.id
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
  if (isStructured.value || pageImageUrl.value) return
  await loadPageImage()
}

async function loadPageImage() {
  if (isStructured.value) return
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
  if (!latestJob.value || rerunnableSteps.value.length === 0) return
  const reason = rerunReason.value.trim()
  if (!reason) return
  actionError.value = ''
  try {
    await request<DocumentJob>(`/api/v1/jobs/${latestJob.value.id}/retry`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({
        reason,
        selected_steps: rerunnableSteps.value,
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
        :title="`本版本解析${statusLabels[latestOutput.status] || latestOutput.status}，部分内容可能不可复核`"
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
      <p>
        <a class="download-link" :href="downloadUrl" download>下载原件</a>
      </p>

      <template v-if="isStructured">
        <h2>结构化预览（原生位置跳转）</h2>
        <p class="candidate-note">
          DOCX/XLSX/CSV/Markdown 材料不生成 PDF 页码，这里按原文顺序展示解析块；点击表格单元格可查看其工作表/单元格或行/列位置。
        </p>
        <el-table
          :data="structuredBlocks"
          empty-text="本版本没有可预览的解析内容"
          height="220"
        >
          <el-table-column label="内容">
            <template #default="scope">
              <strong>{{ scope.row.kind === 'heading' ? '标题' : scope.row.kind === 'table' ? '表格' : scope.row.kind === 'code' ? '代码块' : '段落' }}</strong>
              ：{{ previewText(scope.row.text) }}
            </template>
          </el-table-column>
          <el-table-column label="原生位置" width="260">
            <template #default="scope">
              {{ blockLocation(scope.row.locator as NativeLocator | null) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="scope">
              <el-button
                size="small"
                :aria-label="`定位解析块-${scope.row.id}`"
                @click="focusBlock(scope.row.id)"
              >定位</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="structured-body">
          <div
            v-for="block in structuredBlocks"
            :key="block.id"
            :ref="(element) => setBlockRef(block.id, element)"
            class="structured-block"
            :class="{ target: selectedBlockId === block.id }"
          >
            <p class="structured-locator">{{ blockLocation(block.locator as NativeLocator | null) }}</p>
            <h3 v-if="block.kind === 'heading'" class="structured-heading">{{ block.text }}</h3>
            <p v-else-if="block.kind === 'paragraph'" class="structured-paragraph">{{ block.text }}</p>
            <pre v-else-if="block.kind === 'code'" class="structured-code">{{ block.text }}</pre>
            <table v-else-if="block.kind === 'table' && block.grid.length" class="structured-table">
              <tbody>
                <tr v-for="(row, rowIndex) in block.grid" :key="rowIndex">
                  <td
                    v-for="cell in row"
                    :key="cell.id"
                    class="structured-cell"
                    :class="{ selected: selectedCellId === cell.id }"
                    :aria-label="`表格单元格-${cell.text || '空'}`"
                    @click="selectCell(cell)"
                  >{{ cell.text }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <p v-if="selectedCell" class="candidate-note" role="status">
          选中单元格：{{ cellLocation(selectedCell) }}
        </p>
      </template>

      <template v-else>
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
                width: `${displaySize.width || (selectedPage.width ?? 0) * 2}px`,
                height: `${displaySize.height || (selectedPage.height ?? 0) * 2}px`,
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
      </template>

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
          :disabled="
            !rerunReason.trim() ||
            rerunnableSteps.length === 0 ||
            latestJob.status === 'waiting' ||
            latestJob.status === 'running'
          "
          :aria-label="'重跑解析'"
          @click="rerunParser"
        >{{ isStructured ? '重新解析（产生新输出版本）' : '用新模型重新解析（产生新输出版本）' }}</el-button>
        <p v-if="latestJob.retry_reason" class="candidate-note">
          最近一次重跑原因：{{ latestJob.retry_reason }}
        </p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.download-link {
  color: #1769aa;
}
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
.structured-body {
  border: 1px solid #dcdfe6;
  background: #fff;
  padding: 12px;
  margin-top: 12px;
}
.structured-block {
  border-left: 3px solid transparent;
  padding: 6px 8px;
}
.structured-block.target {
  border-left-color: #1769aa;
  background: rgba(23, 105, 170, 0.08);
}
.structured-locator {
  color: #909399;
  font-size: 12px;
  margin: 0 0 2px;
}
.structured-heading {
  margin: 4px 0;
}
.structured-paragraph {
  margin: 4px 0;
  white-space: pre-wrap;
}
.structured-code {
  background: #f5f7fa;
  padding: 8px;
  overflow: auto;
}
.structured-table {
  border-collapse: collapse;
  margin: 4px 0;
}
.structured-cell {
  border: 1px solid #dcdfe6;
  padding: 4px 8px;
  cursor: pointer;
}
.structured-cell.selected {
  background: rgba(23, 105, 170, 0.2);
  outline: 2px solid #1769aa;
}
</style>
