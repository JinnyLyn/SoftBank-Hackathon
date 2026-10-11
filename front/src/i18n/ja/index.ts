// 일본어 사전. 키는 한국어 원문 그대로, 값은 일본어. 화면 영역별 파일을 합친다
import app from './app'
import steps from './steps'
import api from './api'
import extra from './extra'

const ja: Record<string, string> = { ...app, ...steps, ...api, ...extra }
export default ja
