/**
 * Resume Evaluator AI - Application Logic
 */

const { createApp, ref, computed, onMounted } = Vue;

const app = createApp({
    setup() {
        // State Management
        const AUTH_ERROR = 'AUTH_REQUIRED';
        const isAuthenticated = ref(true);
        const passwordInput = ref('');
        const authError = ref('');
        const authenticating = ref(false);
        const fileInput = ref(null);
        const selectedIndex = ref(null);
        const candidates = ref([]);
        const errorMessage = ref('');
        const showError = ref(false);
        const sortOrder = ref('date'); // 'date' | 'score' | 'category'
        const sortModes = ['date', 'score', 'category'];
        const sortMeta = {
            date: { label: 'Recent', icon: 'time-outline' },
            score: { label: 'Score', icon: 'trophy-outline' },
            category: { label: 'Category', icon: 'albums-outline' }
        };
        const categoryPriority = { AI: 0, BigData: 1, Other: 2 };
        const reanalyzing = ref(false);

        const fetchWithAuth = async (url, options = {}) => {
            const opts = { credentials: 'same-origin', ...options };
            if (options.headers) {
                opts.headers = options.headers;
            }
            const res = await fetch(url, opts);
            if (res.status === 401) {
                isAuthenticated.value = false;
                authError.value = '访问受限，请输入密码';
                throw new Error(AUTH_ERROR);
            }
            return res;
        };

        const deriveCategory = (candidate) => {
            const classification = candidate.result?.domain_classification || '';
            const normalized = classification.trim().toLowerCase();
            if (normalized.includes('ai')) return 'AI';
            if (normalized.includes('big')) return 'BigData';
            return 'Other';
        };

        const sortedCandidates = computed(() => {
            const list = [...candidates.value];
            if (sortOrder.value === 'score') {
                return list.sort((a, b) => {
                    const scoreA = a.result ? (a.result.total_score || 0) : -1;
                    const scoreB = b.result ? (b.result.total_score || 0) : -1;
                    // Descending score, processed items first
                    return scoreB - scoreA;
                });
            }
            if (sortOrder.value === 'category') {
                return list.sort((a, b) => {
                    const catA = deriveCategory(a);
                    const catB = deriveCategory(b);
                    const priorityDiff = (categoryPriority[catA] ?? categoryPriority.Other) - (categoryPriority[catB] ?? categoryPriority.Other);
                    if (priorityDiff !== 0) return priorityDiff;
                    // Preserve original order for candidates in the same category
                    return candidates.value.indexOf(a) - candidates.value.indexOf(b);
                });
            }
            // Default: Keep original order (which is usually date desc from backend)
            // But if we want to be explicit about date:
            // Since backend sends sorted by ctime desc, index order is date order.
            return list;
        });

        const currentSortMeta = computed(() => sortMeta[sortOrder.value]);

        const currentCandidate = computed(() => {
            if (selectedIndex.value === null) return null;
            // selectedIndex tracks the index in the SORTED list now
            return sortedCandidates.value[selectedIndex.value];
        });

        // Load History
        const loadHistory = async () => {
            if (!isAuthenticated.value) return;
            try {
                const res = await fetchWithAuth('/api/candidates');
                if (res.ok) {
                    candidates.value = await res.json();
                }
            } catch (error) {
                if (error.message !== AUTH_ERROR) {
                    console.error('Failed to load history:', error);
                }
            }
        };

        onMounted(() => {
            loadHistory();
        });

        // File Upload Methods
        const triggerFileInput = () => {
            if (!isAuthenticated.value) {
                authError.value = '请先输入密码解锁访问';
                return;
            }
            fileInput.value.click();
        };

        const handleFileUpload = async (event) => {
            const files = event.target.files;
            if (!files.length) return;

            for (let i = 0; i < files.length; i++) {
                const file = files[i];
                // Add placeholder
                const newCandidate = {
                    filename: file.name,
                    id: null,
                    status: 'pending', // pending -> parsing -> analyzing -> done
                    image_urls: [],
                    result: null,
                    pageCount: 0
                };
                candidates.value.unshift(newCandidate); // Add to top

                // Find index to process (it's 0 since we unshifted, but tracking by obj is safer if async changes happen)
                // Actually, let's just pass the object to processCandidate
                processCandidate(newCandidate, file);
            }
            // Clear input
            event.target.value = '';
        };

        // Candidate Processing
        const processCandidate = async (candidate, fileObj) => {
            // No need to lookup by index, use object ref

            try {
                // 1. Upload & Parse PDF
                candidate.status = 'parsing';
                const formData = new FormData();
                formData.append('file', fileObj);

                const uploadRes = await fetchWithAuth('/api/upload_pdf', {
                    method: 'POST',
                    body: formData
                });

                if (!uploadRes.ok) {
                    const errorData = await uploadRes.json().catch(() => ({}));
                    const errorMsg = errorData.detail || `Upload failed (${uploadRes.status})`;
                    throw new Error(`Failed to upload ${candidate.filename}: ${errorMsg}`);
                }
                const uploadData = await uploadRes.json();

                candidate.id = uploadData.id;
                candidate.image_urls = uploadData.image_urls;
                candidate.pageCount = uploadData.page_count;
                candidate.status = 'parsed'; // ready for analysis

                // 2. Analyze with VLM
                candidate.status = 'analyzing';

                const analyzePayload = {
                    candidate_id: candidate.id
                };

                const analyzeRes = await fetchWithAuth('/api/analyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(analyzePayload)
                });

                if (!analyzeRes.ok) {
                    const errorData = await analyzeRes.json().catch(() => ({}));
                    const errorMsg = errorData.detail || `Analysis failed (${analyzeRes.status})`;
                    throw new Error(`Failed to analyze ${candidate.filename}: ${errorMsg}`);
                }
                const analyzeData = await analyzeRes.json();

                candidate.result = analyzeData;
                candidate.name = analyzeData.candidate_name;
                candidate.status = 'done';

            } catch (error) {
                if (error.message === AUTH_ERROR) {
                    const idx = candidates.value.indexOf(candidate);
                    if (idx > -1) candidates.value.splice(idx, 1);
                    return;
                }
                console.error('Processing error:', error);
                candidate.status = 'error';
                handleError(`Error processing ${candidate.filename}: ${error.message}`);
            }
        };

        // Clear Cache
        const clearCache = async () => {
            if (!confirm('Are you sure you want to clear all history? This cannot be undone.')) return;
            try {
                const res = await fetchWithAuth('/api/cache', { method: 'DELETE' });
                if (res.ok) {
                    candidates.value = [];
                    selectedIndex.value = null;
                } else {
                    throw new Error('Failed to clear cache');
                }
            } catch (error) {
                if (error.message === AUTH_ERROR) return;
                handleError(error.message);
            }
        };

        // Delete Candidate
        const deleteCandidate = async (candidate, event) => {
            if (event) event.stopPropagation();
            if (!confirm(`Delete ${candidate.filename}?`)) return;

            try {
                // If it's a pending/placeholder, just remove from list
                if (!candidate.id) {
                    const idx = candidates.value.indexOf(candidate);
                    if (idx > -1) candidates.value.splice(idx, 1);
                    return;
                }

                const res = await fetchWithAuth(`/api/candidates/${candidate.id}`, { method: 'DELETE' });
                if (res.ok) {
                    const idx = candidates.value.indexOf(candidate);
                    if (idx > -1) candidates.value.splice(idx, 1);

                    // Reset selection if the deleted one was selected
                    // This is a bit tricky with sorted list logic + selection index. 
                    // Simplest is to just deselect if current view is invalid? 
                    // Or relying on Vue reactivity might just work but 'selectedIndex' is an integer index.
                    // If we delete item 2, item 3 becomes 2. The view might shift.
                    // Let's just set to null to be safe for now, or handle smoothly.
                    // Actually, if we delete the *currently selected* candidate:
                    if (currentCandidate.value === candidate) {
                        selectedIndex.value = null;
                    } else if (currentCandidate.value) {
                        // We need to re-find the new index of the current candidate in the sorted list?
                        // But 'currentCandidate' is computed from selectedIndex.
                        // If we don't update selectedIndex, we point to a different person.
                        // Ideally we track selection by ID, not index.
                    }

                    // To fix the selection jumping issue properly:
                    // Implementing selection by ID would be better but requires more changes.
                    // For now, let's just deselect.
                    selectedIndex.value = null;

                } else {
                    throw new Error("Failed to delete");
                }
            } catch (e) {
                if (e.message === AUTH_ERROR) return;
                handleError("Delete failed: " + e.message);
            }
        };

        const toggleSort = () => {
            const currentIndex = sortModes.indexOf(sortOrder.value);
            const nextIndex = (currentIndex + 1) % sortModes.length;
            sortOrder.value = sortModes[nextIndex];
            selectedIndex.value = null; // Clear selection to avoid confusion
        };

        // Remove Duplicates
        const removeDuplicates = async () => {
            if (!confirm("Remove duplicate candidates? Keeps the most recent upload.")) return;
            try {
                const res = await fetchWithAuth(`/api/candidates/deduplicate`, { method: 'DELETE' });
                if (res.ok) {
                    const data = await res.json();
                    handleError(`Removed ${data.deleted_count} duplicates.`, 3000);
                    loadHistory(); // Reload list
                } else {
                    throw new Error("Failed to deduplicate");
                }
            } catch (e) {
                if (e.message === AUTH_ERROR) return;
                handleError("Deduplication failed: " + e.message);
            }
        };

        const reanalyzeFromOcr = async () => {
            console.log('reanalyzeFromOcr called');
            if (reanalyzing.value) return;

            try {
                if (!window.confirm('确认基于已有 OCR 文本重新评估所有候选人？这将消耗 API 配额。')) {
                    return;
                }
            } catch (e) {
                console.error('Confirm dialog blocked or failed:', e);
                // Fallback: proceed if confirm fails? No, safer to return.
                return;
            }

            reanalyzing.value = true;
            try {
                console.log('Sending reanalyze request...');
                const res = await fetchWithAuth('/api/candidates/reanalyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ candidate_ids: [] }) // Explicitly empty list to signal "all"
                });
                console.log('Reanalyze response status:', res.status);

                if (!res.ok) {
                    const errorData = await res.json().catch(() => ({}));
                    throw new Error(errorData.detail || `重评失败 (${res.status})`);
                }
                const data = await res.json();
                handleError(`已触发重评：${data.updated}/${data.requested} 份`, 4000);
                await loadHistory();
            } catch (error) {
                console.error('Reanalyze error:', error);
                if (error.message === AUTH_ERROR) return;
                handleError(error.message || '重评失败，请稍后重试');
            } finally {
                reanalyzing.value = false;
            }
        };

        // Selection Methods
        const selectCandidate = (index) => {
            selectedIndex.value = index;
        };

        // Utility Methods
        const getScoreColor = (score) => {
            if (score >= 35) return 'bg-green-100 text-green-700';
            if (score >= 25) return 'bg-yellow-100 text-yellow-700';
            return 'bg-red-100 text-red-700';
        };

        const formatKey = (key) => {
            const map = {
                'tech_foundation': '技术基础 (Tech Foundation)',
                'tech_cognition': '技术认知 (Tech Cognition)',
                'tech_potential': '技术潜力 (Tech Potential)',
                'learning_ability': '学习能力 (Learning Ability)'
            };
            return map[key] || key.replace(/_/g, ' ');
        };

        // Error handling methods
        const handleError = (message, duration = 5000) => {
            errorMessage.value = message;
            showError.value = true;
            setTimeout(() => {
                showError.value = false;
            }, duration);
        };

        const clearError = () => {
            showError.value = false;
            errorMessage.value = '';
        };

        const submitPassword = async () => {
            if (!passwordInput.value) {
                authError.value = '请输入访问密码';
                return;
            }

            authenticating.value = true;
            authError.value = '';

            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify({ password: passwordInput.value.trim() })
                });

                if (!res.ok) {
                    const errorData = await res.json().catch(() => ({}));
                    const detail = errorData.detail || '密码错误';
                    throw new Error(detail);
                }

                isAuthenticated.value = true;
                passwordInput.value = '';
                authError.value = '';
                await loadHistory();
            } catch (error) {
                authError.value = error.message || '登录失败';
            } finally {
                authenticating.value = false;
            }
        };


        return {
            fileInput, triggerFileInput, handleFileUpload,
            candidates, selectedIndex, selectCandidate, currentCandidate,
            getScoreColor, formatKey,
            errorMessage, showError, clearError,
            clearCache, deleteCandidate, removeDuplicates, reanalyzeFromOcr,
            sortedCandidates, sortOrder, toggleSort, currentSortMeta,
            isAuthenticated, passwordInput, submitPassword, authError, authenticating,
            reanalyzing
        };
    }
});

app.config.compilerOptions.isCustomElement = (tag) => tag.includes('ion-icon');
app.mount('#app');
