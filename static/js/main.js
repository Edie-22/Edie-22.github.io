// 缓存清除标记
console.log('Main.js version:20240115a');

// 在诗人选择事件中调用
// 青绿山水生成
async function loadPoemBackground(poetName, poemContent) {
    const $container = $('#generated-illustration');

    // 内容检查
    if (!poemContent || poemContent.trim().length < 5) {
        $container.html(`
            <div class="default-preview">
                <img src="/static/images/default_illustration.jpg">
                <div class="preview-tip">等待诗意输入...</div>
            </div>
        `);
        return;
    }

    // 诗意加载动画
    const loader = $(`
        <div class="colorful-loader">
            <div class="spinner-grow text-success"></div>
            <div class="mt-3">正在绘制「${poetName}」诗意画卷...</div>
            <div class="process-text text-muted mt-2">青绿山水绘制中，约需30秒</div>
        </div>
    `);
    $container.html(loader);

    // 超时控制（60秒）
    const controller = new AbortController();
    const timeoutId = setTimeout(() => {
        controller.abort();
        $container.html(`
            <div class="error-preview">
                <img src="/static/images/default_illustration.jpg">
                <div class="error-tip">生成超时，建议精简诗文内容</div>
            </div>
        `);
    }, 60000);

    try {
        // 发送生成请求
        const response = await fetch(API_BASE_URL+'/generate_ink', {
            method: 'POST',
            signal: controller.signal,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                content: `${poemContent.substring(0, 140)} [青绿山水|工笔重彩|楼阁人物]` // 强化prompt
            })
        });

        const data = await response.json();

        if (data.url) {
            // 成功加载插画
            $container.html(`
                <div class="illustration-card">
                    <img src="${data.url}" 
                         alt="${poetName}诗意插画"
                         class="scroll-reveal"
                         loading="lazy"
                         onerror="this.onerror=null;this.src='/static/images/default_illustration.jpg'">
                    <div class="illustration-meta">
                        <span class="poet-name">${poetName}</span>
                        <span class="badge bg-warning">AI生成</span>
                    </div>
                </div>
            `);
        } else {
            throw new Error(data.error || '画师未能完成此作');
        }
    } catch (error) {
        // 错误处理
        const errorMsg = error.name === 'AbortError'
            ? '绘制超时：繁复画工需更长时间'
            : `生成失败：${error.message}`;

        $container.html(`
            <div class="error-preview animate__animated animate__shakeX">
                <img src="/static/images/default_illustration.jpg">
                <div class="error-tip">${errorMsg}</div>
            </div>
        `);
    } finally {
        clearTimeout(timeoutId);
    }
}

// 使用IIFE包裹全局声明
(function() {
    let currentPoet = null;
    let animationInterval;
    let isPlaying = false;
    let poemCountChart = null;

    // 缓存变量
    const poemSearch = $('#poemSearch');
    const poemSuggestions = $('#poemSuggestions');
    const poemDisplay = $('#poem-display');

    // 初始化地图
    const map = L.map('map').setView([34.0, 108.0], 5);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap'
    }).addTo(map);

    // 加载诗人列表
    $.get(API_BASE_URL+'/api/poets').then(poets => {
        const select = $('#poetSelect');
        poets.forEach(poet => {
            if (poet.name !== '杜甫') {
                select.append(new Option(`${poet.name} `, poet.name));
            }
        });
    });

    // 监听输入框输入事件
    poemSearch.on('input', function () {
        const keyword = $(this).val().trim();
        const poetName = $('#poetSelect').val();
        if (keyword && poetName) {
            $.ajax({
                url: '/api/search_poem_titles',
                type: 'GET',
                data: {
                    title: keyword,
                    author: poetName
                },
                success: function (titles) {
                    if (titles.length) {
                        poemSuggestions.empty();
                        titles.forEach(title => {
                            const item = $('<a class="dropdown-item" href="#">').text(title);
                            item.on('click', function () {
                                poemSearch.val(title);
                                poemSuggestions.hide();
                                searchCurrentPoetPoem(title);
                            });
                            poemSuggestions.append(item);
                        });
                        poemSuggestions.show();
                    } else {
                        poemSuggestions.hide();
                    }
                },
                error: function (xhr) {
                    console.error('搜索提示失败：', xhr.statusText);
                    poemSuggestions.hide();
                }
            });
        } else {
            poemSuggestions.hide();
        }
    });

    //搜索按钮事件绑定
    $('#searchBtn').on('click', function() {
        searchCurrentPoetPoem();
    });

    // 诗人选择处理器
    $('#poetSelect').on('change', async function () {
        currentPoet = this.value;
        if (!currentPoet) return;

        try {
            // 获取数据
            const [poetData, wordcloud] = await Promise.all([
                $.get(API_BASE_URL+`/api/poet/${encodeURIComponent(currentPoet)}`),
                $.get(API_BASE_URL+`/wordcloud/${encodeURIComponent(currentPoet)}`)
            ]);

            // 渲染默认作品
            $('#default-poem').html(`
            ${poetData.poems.slice(0, 1).map(p => `
                <div class="poem-detail">
                    <h4>《${p.title}》</h4>
                    ${p.data ? `<div class="text-muted mb-2">创作于${p.data}</div>` : ''}
                    <pre class="content">${p.content}</pre>
                    ${p.trans_content ? `<div class="translation alert alert-light mt-2">${p.trans_content}</div>` : ''}
                    ${p.zhu ? `<div class="annotation alert alert-info mt-2">${p.zhu}</div>` : ''}
                    ${p.appear ? `<div class="appreciation card mt-2"><div class="card-body">${p.appear}</div></div>` : ''}
                </div>
            `).join('')}
        `);

            // 清除之前的标记和路径
            map.eachLayer(layer => {
                if (layer instanceof L.Marker || layer instanceof L.Polyline) {
                    map.removeLayer(layer);
                }
            });

            // 路径生成逻辑
            const validLocations = poetData.locations
               .filter(loc => loc.lat && loc.lon &&
                    !isNaN(loc.lat) && !isNaN(loc.lon) &&
                    loc.lat > 15 && loc.lat < 55 &&  // 中国大陆范围
                    loc.lon > 73 && loc.lon < 135)
               .sort((a, b) => a.year - b.year);
            const timelineCoords = validLocations.map(loc => [loc.lat, loc.lon]);
            // 设置安全默认视角（长安坐标）
            if (validLocations.length === 0) {
                L.marker([34.0, 108.0], {
                    title: '默认定位（长安）'
                }).addTo(map);
            }

            if (timelineCoords.length > 0) {
                map.setView([timelineCoords[0][0], timelineCoords[0][1]], 5); // 设置初始视图
            }

            if (timelineCoords.length > 1) {
                const pathAnimation = createAnimatedPath(timelineCoords, map, poetData.locations);
                if (isPlaying) {
                    clearInterval(animationInterval);
                    animationInterval = setInterval(() => {
                        pathAnimation();
                    }, 700);
                }
            }

            // 更新词云
            $('#wordcloud').attr('src', `data:image/png;base64,${wordcloud}`);

            // 更新诗人照片
            const imageName = currentPoet.replace(/[\\/:*?"<>|]/g, '');  // 清理非法文件名
            const imageUrl = `/static/images/${imageName}.png`;
            $('#poet-image').attr('src', imageUrl)
               .on('error', function() {  // 图片加载失败时替换为默认
                    $(this).attr('src', '/static/images/default.png');
                });

            // 更新诗人姓名
            $('#poet-name').text(currentPoet);

            // 更新诗人生卒年
            $('#poet-lifetime').text(`${poetData.info.birth} - ${poetData.info.death}`);

            // 更新诗人简介
            $('#poet-bio').html(`<p>${poetData.info.bio}</p>`);

            // 显示默认作品，隐藏搜索结果
            $('#default-poem').show();
            $('#poem-results').hide();

            // 绘制三个折线图
            renderTimeSeriesChart(currentPoet);
            renderAnshiChart(currentPoet);
            renderPoliticalChart(currentPoet);

            // 更新意象分析
            updateImagery(currentPoet);

            // 获取第一首诗的内容
            const firstPoemContent = poetData.poems.length > 0 ? poetData.poems[0].content : '';

        } catch (error) {
            console.error('Error:', error);
        }
    });

    // 路径动画功能
    function createAnimatedPath(coordinates, map, locations) {
        const animatedPath = L.polyline([], {
            color: '#598288',
            weight: 3,
            dashArray: '10 5'
        }).addTo(map);

        let currentIndex = 0;

        function updatePath() {
            if (currentIndex < coordinates.length) {
                animatedPath.addLatLng(coordinates[currentIndex]);

                // 显示对应的标记点
                const loc = locations[currentIndex];
                if (loc.lat && loc.lon) {
                    const marker = L.circleMarker([loc.lat, loc.lon], {
                        radius: 8,
                        fillColor: '#ba645d',
                        color: '#000',
                        weight: 1,
                        opacity: 1,
                        fillOpacity: 1
                    })
                       .addTo(map)
                       .bindPopup(`
                    <b>${loc.name}</b><br>
                    时间：${loc.year || '未知'}<br>
                    事件：${loc.event || ''}
                `);
                }

                currentIndex++;
                if (currentIndex === coordinates.length) {
                    // 循环播放
                    currentIndex = 0;
                    animatedPath.setLatLngs([]);
                    map.eachLayer(layer => {
                        if (layer instanceof L.Marker) {
                            map.removeLayer(layer);
                        }
                    });
                }
            }
        }

        // 交互事件增强
        animatedPath.on('click', (e) => {
            const popup = L.popup()
               .setLatLng(e.latlng)
               .setContent(`轨迹点 ${currentIndex}`)
               .openOn(map);
        });

        return updatePath;
    }

    // 初始化显示首篇作品
    $('#poetSelect').trigger('change');

    // 修改搜索函数以支持选择诗名
    function searchCurrentPoetPoem(selectedTitle) {
        const keyword = selectedTitle || $('#poemSearch').val().trim();

        if (!keyword) {
            poemDisplay.html('<div class="alert alert-warning">请输入搜索词</div>');
            return;
        }

        const poetName = $('#poetSelect').val();
        if (!poetName) {
            poemDisplay.html('<div class="alert alert-warning">请先选择诗人</div>');
            return;
        }

        $.ajax({
            url: API_BASE_URL+'/api/search_poem',
            type: 'GET',
             data: {  // 参数传递
                title: keyword,
                 author: poetName
             },
            success: function (poems) {
                if (poems.error) {
                    poemDisplay.html(`<div class="alert alert-danger">${poems.error}</div>`);
                    return;
                }

                if (poems.length) {
                    const poem = poems[0];
                    const poemContent = poem.content || '';
                    if(poemContent) {
                        loadPoemBackground(currentPoet, poemContent);  // 触发背景生成
                    }
                    const poemHtml = `
                        <div class="searched-item mb-4 p-3 border rounded text-center">
                            <h5>《${poem.title}》${poem.data ? `<small class="text-muted">(${poem.data})</small>` : ''}</h5>
                            <pre class="content">${poem.content}</pre>
                        </div>
                        <ul class="nav nav-tabs" id="poemTabs" role="tablist">
                            <li class="nav-item" role="presentation">
                                <button class="nav-link active" id="translation-tab" data-bs-toggle="tab" data-bs-target="#translation" type="button" role="tab" aria-controls="translation" aria-selected="true">翻译</button>
                            </li>
                            <li class="nav-item" role="presentation">
                                <button class="nav-link" id="annotation-tab" data-bs-toggle="tab" data-bs-target="#annotation" type="button" role="tab" aria-controls="annotation" aria-selected="false">注释</button>
                            </li>
                            <li class="nav-item" role="presentation">
                                <button class="nav-link" id="background-tab" data-bs-toggle="tab" data-bs-target="#background" type="button" role="tab" aria-controls="background" aria-selected="false">创作背景</button>
                            </li>
                        </ul>
                        <div class="tab-content" id="poemTabContent" style="max-height: 200px; overflow-y: auto;">
                            <div class="tab-pane fade show active" id="translation" role="tabpanel" aria-labelledby="translation-tab">
                                ${poem.trans_content ? `<div class="translation alert alert-light">${poem.trans_content}</div>` : '<div class="text-muted">暂无翻译内容</div>'}
                            </div>
                            <div class="tab-pane fade" id="annotation" role="tabpanel" aria-labelledby="annotation-tab">
                                ${poem.zhu ? `<div class="annotation alert alert-info">${poem.zhu}</div>` : '<div class="text-muted">暂无注释内容</div>'}
                            </div>
                            <div class="tab-pane fade" id="background" role="tabpanel" aria-labelledby="background-tab">
                                ${poem.appear ? `<div class="appreciation alert alert-warning">${poem.appear}</div>` : '<div class="text-muted">暂无创作背景内容</div>'}
                            </div>
                        </div>
                    `;
                    poemDisplay.html(poemHtml);

                    // 初始化选卡切换功能
                    const tabTriggerList = [].slice.call(document.querySelectorAll('#poemTabs button'));
                    tabTriggerList.forEach(function (tabTriggerEl) {
                        new bootstrap.Tab(tabTriggerEl);
                    });
                } else {
                    poemDisplay.html('<div class="text-center text-muted py-3">未找到相关作品</div>');
                }
            },
            error: function (xhr) {
                poemDisplay.html(`<div class="alert alert-danger">搜索失败：${xhr.statusText}</div>`);
            }
        });
    }


    // 播放按钮点击事件
    $('#playButton').on('click', async function () {
        isPlaying = true;
        $(this).prop('disabled', true);
        $('#pauseButton').prop('disabled', false);

        try {
            const poetData = await $.get(API_BASE_URL+`/api/poet/${encodeURIComponent(currentPoet)}`);
            const timelineCoords = poetData.locations
               .sort((a, b) => a.year - b.year)
               .map(loc => [loc.lat, loc.lon]);

            if (timelineCoords.length > 1) {
                const pathAnimation = createAnimatedPath(timelineCoords, map, poetData.locations);
                clearInterval(animationInterval);
                animationInterval = setInterval(() => {
                    pathAnimation();
                }, 700); //速度设置0.7秒
            }
        } catch (error) {
            console.error('Error fetching poet data:', error);
        }
    });

    // 暂停按钮点击事件
    $('#pauseButton').on('click', function () {
        isPlaying = false;
        $(this).prop('disabled', true);
        $('#playButton').prop('disabled', false);
        clearInterval(animationInterval);
    });

    // 监听搜索按钮点击事件
    $('#searchBtn').on('click', function () {
        poemSuggestions.hide();
        searchCurrentPoetPoem();
    });

    // 独立意象更新函数
    function updateImagery(poet) {
        // 初始化三个词云容器
        const tabContainers = {
            '#overallCloud': { data: [], chart: null },
            '#preAnshiCloud': { data: [], chart: null },
            '#postAnshiCloud': { data: [], chart: null }
        };

        // 加载整体词云
        fetch(API_BASE_URL+`/api/imagery_cloud?poet=${encodeURIComponent(poet)}`)
            .then(res => res.json())
            .then(data => {
                tabContainers['#overallCloud'].data = data;
                renderTabCloud('#overallCloud', data, '整体意象分析');
            });

        // 加载安史之乱前
        fetch(API_BASE_URL+`/api/period_imagery/${encodeURIComponent(poet)}?cond=${encodeURIComponent('year < 755')}`)
            .then(res => res.json())
            .then(data => {
                tabContainers['#preAnshiCloud'].data = data;
                renderTabCloud('#preAnshiCloud', data, '动乱前意象特征');
            });

        // 加载安史之乱后
        fetch(API_BASE_URL+`/api/period_imagery/${encodeURIComponent(poet)}?cond=${encodeURIComponent('year >= 755')}`)
            .then(res => res.json())
            .then(data => {
                tabContainers['#postAnshiCloud'].data = data;
                renderTabCloud('#postAnshiCloud', data, '战后意象变迁');
            });

        // 选卡切换处理
        $('#imageryTabs button').on('shown.bs.tab', function (e) {
            const target = $(e.target).attr('data-bs-target');
            const container = tabContainers[target];
            if (container.chart) {
                container.chart.resize();
            }
        });

        // 通用词云渲染函数
        function renderTabCloud(selector, data, title = '') {
            const chartDom = document.querySelector(selector);
            if (tabContainers[selector].chart) {
                tabContainers[selector].chart.dispose();
            }

            const chart = echarts.init(chartDom);
            tabContainers[selector].chart = chart;

            // 计算独立透明度基准
            const localMaxValue = data.length ? Math.max(...data.map(d => d.value)) : 1;

            const options = {
                title: {
                    text: title,
                    left: 'center',
                    textStyle: {
                        fontFamily: 'STKaiti',
                        fontSize: 16,
                        color: '#634d40'
                    }
                },
                tooltip: {
                    formatter: ({ name }) => `${name}<br>出现频次: ${data.find(d => d.name === name)?.value || 0}`
                },
                series: [{
                    type: 'wordCloud',
                    shape: 'diamond',
                    sizeRange: [25, 120],
                    rotationRange: [0, 0],
                    gridSize: 12,
                    textStyle: {
                        fontFamily: 'STKaiti',
                        fontWeight: 'bold',
                        color: (params) => {
                            const opacity = 0.3 + (params.value / localMaxValue * 0.7);
                            return selector === '#preAnshiCloud' ?
                                `rgba(23,107,152,${opacity})` : // 前期蓝色系
                                `rgba(120,50,5,${opacity})`;   // 后期棕色系
                        }
                    },
                    emphasis: {
                        focus: 'self',
                        textStyle: { shadowBlur: 10 }
                    },
                    data: data
                }]
            };

            chart.setOption(options);
            window.addEventListener('resize', () => chart.resize());
        }
    }

    // 图表1：原有时间序列折线图
    function renderTimeSeriesChart(poetName) {
        fetch(API_BASE_URL+`/api/poet_annual_counts/${encodeURIComponent(poetName)}`)
            .then(r => r.json())
            .then(data => {
                new Chart(document.getElementById('time-series-chart'), {
                    type: 'line',
                    data: {
                        labels: data.map(d => d.year),
                        datasets: [{
                            label: '作品数量',
                            data: data.map(d => d.count),
                            borderColor: '#6f8b74'
                        }]
                    }
                });
            });
    }

    // 图表2：安史之乱时期块状图
    function renderAnshiChart(poetName) {
        fetch(API_BASE_URL+`/api/poet_anshi_periods/${encodeURIComponent(poetName)}`)
            .then(r => r.json())
            .then(data => {
                new Chart(document.getElementById('anshi-periods-chart'), {
                    type: 'bar',
                    data: {
                        labels: ['安史之乱前 (750-755)', '动乱中期 (755-757)', '战后恢复 (757-763)'],
                        datasets: [{
                            label: '作品总数',
                            data: [data.pre_anshi, data.mid_anshi, data.post_anshi],
                            backgroundColor: ['#dcb988', '#c76f66', '#6f8b74']
                        }]
                    },
                    options: { indexAxis: 'y' } // 横向展示
                });
            });
    }

    // 图表3：政治身份对比
    function renderPoliticalChart(poetName) {
        fetch(API_BASE_URL+`/api/poet_office_periods/${encodeURIComponent(poetName)}`)
            .then(r => r.json())
            .then(data => {
                if (!Array.isArray(data)) { // 验证数据格式
                    throw new Error('数据格式错误，期待数组');
                }

                const labels = data.map(d => `${d.period}（${d.type}）`);
                const counts = data.map(d => d.count);

                new Chart(document.getElementById('political-status-chart'), {
                    type: 'bar',
                    data: {
                        labels: data.map(d => d.period + ' ' + d.type),
                        datasets: [{
                            label: '作品数量',
                            data: data.map(d => d.count),
                            backgroundColor: '#5c8194'
                        }]
                    },
                    options: {
                        indexAxis: 'y' // 横向显示
                    }
                });
            })
            .catch(e => console.error('图表加载失败:', e));
    }

})();